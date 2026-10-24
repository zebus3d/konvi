"""Pruebas del motor sin ffmpeg (milisegundos):  python3 -m unittest tests.test_core"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from konvi.core import command, naming, presets, sequence
from konvi.core.jobs import Job, Status, expand_paths
from konvi.core.probe import AudioInfo, MediaInfo, VideoInfo, format_duration, parse, sequence_info
from konvi.core.progress import Progress, format_eta

P = presets.builtin_by_id()


def media(w=1920, h=1080, fps=25.0, pix="yuv420p", depth=8, interlaced=False, audio=(("aac", 2, 128000),),
          subs=(), duration=60.0, vcodec="h264"):
    video = VideoInfo(vcodec, w, h, fps, pix, depth, interlaced) if w else None
    return MediaInfo("/v/in.mp4", duration, 10_000_000, "mov,mp4", video,
                     tuple(AudioInfo(c, ch, br, "") for c, ch, br in audio), tuple(subs))


def args_for(info, preset, **kw):
    return command.build(info, preset, "/v/out." + preset.container, **kw)


def value(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


class CommandTests(unittest.TestCase):
    def test_h264_basico(self):
        a = args_for(media(), P["mp4_h264"])
        self.assertEqual(value(a, "-c:v"), "libx264")
        self.assertEqual(value(a, "-crf"), "20")
        self.assertEqual(value(a, "-pix_fmt"), "yuv420p")
        self.assertIn("+faststart", a)
        self.assertNotIn("-vf", a)                  # nada que reducir ni recortar
        self.assertEqual(a[-1], "/v/out.mp4")

    def test_reduce_pero_nunca_agranda(self):
        self.assertEqual(value(args_for(media(3840, 2160), P["mp4_h264_1080"]), "-vf"),
                         "scale=-2:1080:flags=lanczos")
        self.assertNotIn("-vf", args_for(media(1280, 720), P["mp4_h264_1080"]))

    def test_vertical_limita_el_lado_corto(self):
        self.assertEqual(value(args_for(media(1080, 1920), P["mp4_h264_720"]), "-vf"),
                         "scale=720:-2:flags=lanczos")

    def test_tamano_impar_se_recorta_a_par(self):
        self.assertEqual(value(args_for(media(1279, 721), P["mp4_h264"]), "-vf"),
                         "crop=trunc(iw/2)*2:trunc(ih/2)*2")
        self.assertNotIn("-vf", args_for(media(1279, 721), P["mkv_ffv1"]))   # FFV1 no lo necesita

    def test_entrelazado_y_fps(self):
        vf = value(args_for(media(fps=50, interlaced=True), P["web_whatsapp"]), "-vf")
        self.assertTrue(vf.startswith("bwdif"))
        self.assertIn("fps=30", vf)

    def test_mpeg2_ajusta_cadencias_raras(self):
        # la cadencia de tele más cercana a 15
        self.assertIn("fps=23.976", value(args_for(media(fps=15), P["mpeg_mpeg2"]), "-vf"))

    def test_diez_bits(self):
        self.assertEqual(value(args_for(media(pix="yuv420p10le", depth=10), P["mp4_h265"]), "-pix_fmt"),
                         "yuv420p10le")
        self.assertEqual(value(args_for(media(pix="yuv420p10le", depth=10), P["mp4_h264"]), "-pix_fmt"),
                         "yuv420p")

    def test_h265_en_mp4_lleva_hvc1(self):
        self.assertEqual(value(args_for(media(), P["mp4_h265"]), "-tag:v"), "hvc1")
        self.assertNotIn("-tag:v", args_for(media(), P["mkv_h265"]))

    def test_audio_compatible_se_copia(self):
        a = args_for(media(), P["mp4_h264"])
        self.assertEqual(value(a, "-c:a:0"), "copy")

    def test_audio_demasiado_grande_se_recodifica(self):
        a = args_for(media(audio=(("aac", 2, 320000),)), P["web_whatsapp"])
        self.assertEqual(value(a, "-c:a:0"), "aac")
        self.assertEqual(value(a, "-b:a:0"), "128k")

    def test_bajar_a_estereo(self):
        a = args_for(media(audio=(("aac", 6, 128000),)), P["web_whatsapp"])
        self.assertEqual(value(a, "-ac:a:0"), "2")

    def test_opus_5_1(self):
        a = args_for(media(audio=(("ac3", 6, 448000),)), P["webm_av1"])
        self.assertEqual(value(a, "-filter:a:0"), "aformat=channel_layouts=5.1")

    def test_todas_las_pistas_y_subtitulos_en_mkv(self):
        info = media(audio=(("aac", 2, 128000), ("ac3", 6, 448000)), subs=("subrip", "mov_text"))
        a = args_for(info, P["mkv_h264"])
        self.assertIn("0:a:1", a)
        self.assertEqual(value(a, "-c:s:1"), "srt")
        self.assertNotIn("-sn", a)
        self.assertIn("-sn", args_for(info, P["mp4_h264"]))

    def test_sin_audio(self):
        self.assertIn("-an", args_for(media(audio=()), P["mp4_h264"]))

    def test_solo_audio(self):
        a = args_for(media(), P["audio_mp3"])
        self.assertIn("-vn", a)
        self.assertEqual(value(a, "-c:a:0"), "libmp3lame")

    def test_errores_claros(self):
        self.assertIn("Solo audio", command.check(media(w=0), P["mp4_h264"]))
        self.assertIn("no tiene sonido", command.check(media(audio=()), P["audio_mp3"]))
        self.assertIn("demasiado largo", command.check(media(duration=7200), P["web_discord"]))
        with self.assertRaises(command.ConvertError):
            args_for(media(w=0), P["mp4_h264"])

    def test_tamano_objetivo_cabe(self):
        info = media(duration=60)
        p = P["web_discord"]
        kbps = command.target_video_kbps(info, p)
        # el peor caso (bitrate máximo + búfer de 1 s) cabe en el tamaño
        worst_kbit = kbps * (info.duration + 1) + p.audio_kbps * info.duration
        self.assertLessEqual(worst_kbit, p.target_mb * 1024 * 8)
        a = args_for(info, p)
        self.assertEqual(value(a, "-maxrate"), f"{kbps}k")
        self.assertNotEqual(value(a, "-c:a:0"), "copy")

    def test_sin_perdida(self):
        a = args_for(media(pix="rgb24", w=1279), P["mp4_lossless"])
        self.assertEqual(value(a, "-c:v"), "libx264rgb")
        self.assertEqual(value(a, "-qp"), "0")
        self.assertNotIn("-vf", a)                    # RGB: no hace falta recortar
        a = args_for(media(pix="yuv420p10le", depth=10), P["mp4_lossless"])
        self.assertEqual(value(a, "-c:v"), "libx264")
        self.assertNotIn("-pix_fmt", a)               # conserva el formato de color del original

    def test_tarjeta_grafica(self):
        hw = command.Hardware(nvenc=frozenset({"h264", "h265"}))
        a = args_for(media(), P["mp4_h264"], hardware=hw, use_hardware=True)
        self.assertEqual(value(a, "-c:v"), "h264_nvenc")
        self.assertTrue(command.uses_hardware(a))
        # lo que la tarjeta no hace, va por el procesador
        self.assertEqual(value(args_for(media(), P["mp4_av1"], hardware=hw, use_hardware=True), "-c:v"),
                         "libsvtav1")
        self.assertEqual(value(args_for(media(), P["mp4_lossless"], hardware=hw, use_hardware=True), "-c:v"),
                         "libx264")
        vaapi = command.Hardware(vaapi=frozenset({"h264"}))
        a = args_for(media(), P["mp4_h264"], hardware=vaapi, use_hardware=True)
        self.assertIn("hwupload", value(a, "-vf"))
        self.assertLess(a.index("-vaapi_device"), a.index("-i"))

    # --- lo adoptado de los presets de HandBrake

    def test_tamano_final_elige_la_resolucion_por_la_duracion(self):
        self.assertEqual([command.auto_height(k) for k in (8000, 3000, 1200, 500, 100)],
                         [1080, 720, 540, 360, 240])
        # más largo, más pequeño (como «Social 10 MB 30 Seconds 720p» de HandBrake, etc.)
        cases = (("web_email", 20, None), ("web_discord", 20, None), ("web_discord", 60, "720"),
                 ("web_discord", 150, "360"), ("web_discord", 300, "240"))
        for pid, seconds, height in cases:
            with self.subTest(f"{pid} {seconds}s"):
                vf = value(args_for(media(duration=seconds), P[pid]), "-vf") or ""
                if height:
                    self.assertIn(f"scale=-2:{height}", vf)
                else:
                    self.assertNotIn("scale", vf)                   # cabe en 1080p: se deja

    def test_h265_y_av1_siempre_en_10_bits(self):
        self.assertEqual(value(args_for(media(), P["mp4_h265"]), "-pix_fmt"), "yuv420p10le")
        self.assertEqual(value(args_for(media(), P["mp4_av1"]), "-pix_fmt"), "yuv420p10le")
        hw = command.Hardware(nvenc=frozenset({"h264", "h265"}))
        self.assertEqual(value(args_for(media(), P["mp4_h265"], hardware=hw, use_hardware=True), "-pix_fmt"),
                         "p010le")
        self.assertEqual(value(args_for(media(), P["mp4_h264"]), "-pix_fmt"), "yuv420p")

    def test_afinado_de_x265_y_svt_av1(self):
        self.assertIn(command.X265_TUNE, value(args_for(media(), P["mp4_h265"]), "-x265-params"))
        self.assertEqual(value(args_for(media(), P["mp4_av1"]), "-svtav1-params"), command.SVTAV1_TUNE)

    def test_nvenc_con_su_escala_de_calidad(self):
        hw = command.Hardware(nvenc=frozenset({"h264", "h265"}))
        a = args_for(media(), P["mp4_h265"], hardware=hw, use_hardware=True)
        self.assertEqual(value(a, "-cq"), "25")                     # CRF 22 por CPU ≈ CQ 25 por GPU (VMAF)
        a = args_for(media(), P["mp4_h264"], hardware=hw, use_hardware=True)
        self.assertEqual(value(a, "-cq"), "23")                     # CRF 20 por CPU ≈ CQ 23 por GPU
        self.assertEqual(value(a, "-rc-lookahead"), "10")

    def test_mkv_copia_el_audio_tal_cual(self):
        info = media(audio=(("dts", 6, 1536000), ("aac", 2, 128000)))
        a = args_for(info, P["mkv_h265"])
        self.assertEqual((value(a, "-c:a:0"), value(a, "-c:a:1")), ("copy", "copy"))

    def test_tele_estereo_mas_envolvente(self):
        a = args_for(media(audio=(("eac3", 6, 640000),)), P["device_tv"])
        self.assertEqual((value(a, "-c:a:0"), value(a, "-ac:a:0"), value(a, "-c:a:1")), ("aac", "2", "copy"))
        a = args_for(media(audio=(("aac", 6, 384000),)), P["device_tv"])
        self.assertEqual((value(a, "-c:a:1"), value(a, "-b:a:1")), ("ac3", "640k"))
        a = args_for(media(audio=(("aac", 2, 128000),)), P["device_tv"])
        self.assertNotIn("-c:a:1", a)                               # estéreo: una sola pista

    def test_pcm_de_24_bits_para_editar(self):
        a = args_for(media(), P["edit_dnxhr_hq"])
        self.assertEqual(value(a, "-c:a:0"), "pcm_s24le")
        a = args_for(media(audio=(("pcm_s24le", 2, 0),)), P["edit_dnxhr_hq"])
        self.assertEqual(value(a, "-c:a:0"), "copy")

    def test_dvd(self):
        a = args_for(media(), P["mpeg_dvd"])
        self.assertEqual(value(a, "-target"), "pal-dvd")
        self.assertEqual(value(a, "-aspect"), "16:9")
        self.assertEqual(value(args_for(media(720, 576), P["mpeg_dvd"]), "-aspect"), "4:3")

    def test_todos_los_perfiles_generan_comando(self):
        for p in presets.BUILTIN:
            with self.subTest(p.id):
                info = media(duration=20)
                if p.target_mb is None or command.check(info, p) is None:
                    self.assertEqual(args_for(info, p)[0], "ffmpeg")


class PresetTests(unittest.TestCase):
    def test_ids_unicos_y_categorias_conocidas(self):
        ids = [p.id for p in presets.BUILTIN]
        self.assertEqual(len(ids), len(set(ids)))
        cats = {c.id for c in presets.CATEGORIES}
        for p in presets.BUILTIN:
            self.assertIn(p.category, cats)
            self.assertTrue(p.description)
        for c in presets.CATEGORIES:
            if c.id != "custom":
                self.assertTrue(any(p.category == c.id for p in presets.BUILTIN), c.id)
        self.assertIn(presets.DEFAULT_ID, ids)

    def test_guardar_y_leer_perfiles_propios(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "presets.json"
            mine = replace(P["mp4_h264"], id="custom_1", name="Mío", quality=26, category="custom", custom=True)
            presets.save_custom(path, [mine])
            self.assertEqual(presets.load_custom(path), [mine])
            self.assertEqual(presets.new_custom_id([mine]), "custom_2")

    def test_fichero_de_perfiles_roto_no_tumba_nada(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "presets.json"
            path.write_text('[{"id": "x"}, 3, "basura", {"id": "ok", "category": "mp4", "name": "Bien",'
                            ' "description": "", "container": "mp4", "campo_nuevo": 1}]')
            loaded = presets.load_custom(path)
            self.assertEqual([p.id for p in loaded], ["ok"])
            self.assertEqual(presets.load_custom(Path(d) / "no_existe.json"), [])


class ProbeTests(unittest.TestCase):
    def test_parse(self):
        info = parse({
            "format": {"duration": "12.5", "size": "1000", "format_name": "mov"},
            "streams": [
                {"codec_type": "video", "codec_name": "mjpeg", "width": 300, "height": 300,
                 "disposition": {"attached_pic": 1}},
                {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
                 "avg_frame_rate": "30000/1001", "pix_fmt": "yuv420p10le", "field_order": "progressive",
                 "side_data_list": [{"rotation": -90}]},
                {"codec_type": "audio", "codec_name": "aac", "channels": 6, "bit_rate": "384000",
                 "tags": {"language": "spa"}},
                {"codec_type": "subtitle", "codec_name": "mov_text"},
            ]}, "/x.mov")
        v = info.video
        self.assertEqual((v.codec, v.width, v.height, v.bit_depth), ("hevc", 1080, 1920, 10))
        self.assertAlmostEqual(v.fps, 29.97, places=2)
        self.assertEqual(info.audio[0].channels, 6)
        self.assertEqual(info.subtitles, ("mov_text",))
        self.assertIn("H.265", info.summary())
        self.assertIn("AAC 5.1", info.summary())

    def test_duracion(self):
        self.assertEqual(format_duration(83), "1:23")
        self.assertEqual(format_duration(3723), "1:02:03")


class ProgressTests(unittest.TestCase):
    def test_bloques_cortados_por_cualquier_sitio(self):
        p = Progress(100)
        text = "frame=10\nout_time_us=25000000\nspeed=2.5x\nprogress=continue\n"
        for i in range(0, len(text), 7):
            p.feed(text[i:i + 7])
        self.assertAlmostEqual(p.fraction, 0.25)
        self.assertAlmostEqual(p.eta, 30.0)
        p.feed("out_time_us=N/A\nprogress=end\n")
        self.assertEqual(p.fraction, 1.0)

    def test_eta(self):
        self.assertEqual(format_eta(None), "")
        self.assertEqual(format_eta(42), "42 s")
        self.assertEqual(format_eta(125), "2 min 05 s")
        self.assertEqual(format_eta(7300), "2 h 01 min")


class NamingTests(unittest.TestCase):
    def test_no_pisa_nada(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "clip.mp4"
            src.touch()
            self.assertEqual(naming.output_path(src, "mp4").name, "clip (2).mp4")      # el original
            self.assertEqual(naming.output_path(src, "mkv").name, "clip.mkv")
            self.assertEqual(naming.output_path(src, "mkv", taken=[Path(d) / "clip.mkv"]).name,
                             "clip (2).mkv")                                         # otro de la cola
            (Path(d) / "clip.mkv").touch()
            self.assertEqual(naming.output_path(src, "mkv").name, "clip (2).mkv")
            self.assertEqual(naming.output_path(src, "mkv", folder=Path(d) / "otra").parent.name, "otra")

    def test_temporal_conserva_la_extension(self):
        self.assertEqual(naming.temp_path(Path("/a/clip (2).mp4")).name, "clip (2).konvi-tmp.mp4")


class JobTests(unittest.TestCase):
    def test_ciclo_de_estados(self):
        job = Job(Path("/v/a.mp3"), P["mp4_h264"])
        self.assertEqual(job.status, Status.PROBING)
        job.set_probed(media(w=0))
        self.assertEqual(job.status, Status.INVALID)          # un mp3 con perfil de vídeo
        job.set_preset(P["audio_mp3"])
        self.assertEqual(job.status, Status.READY)
        job.status = Status.DONE
        job.reset()
        self.assertEqual(job.status, Status.READY)
        bad = Job(Path("/v/x"), P["mp4_h264"])
        bad.set_probed(None, "No se lee")
        self.assertEqual((bad.status, bad.message), (Status.INVALID, "No se lee"))

    def test_carpetas(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "sub").mkdir()
            for name in ("a.MP4", "sub/b.mkv", "notas.txt", "c.konvi-tmp.mp4"):
                (d / name).touch()
            self.assertEqual(sorted(p.name for p in expand_paths([d])), ["a.MP4", "b.mkv"])


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, *names):
        for n in names:
            (self.dir / n).write_bytes(b"")
        return [self.dir / n for n in names]

    def test_agrupa_por_prefijo_y_relleno(self):
        files = self.touch("render_0001.png", "render_0002.png", "render_10000.png",
                           "otra.0001.exr", "otra.0002.exr", "suelta.png", "notas.txt")
        found = {s.name: s for s in sequence.find(files)}
        self.assertEqual(set(found), {"render_####.png", "otra.####.exr"})
        self.assertEqual(found["render_####.png"].frames, (1, 2, 10000))
        self.assertEqual(found["render_####.png"].stem, "render")

    def test_sin_ceros_delante(self):
        seq, = sequence.find(self.touch(*(f"f{n}.jpg" for n in range(1, 11))))
        self.assertEqual((seq.padding, seq.frames), (1, tuple(range(1, 11))))
        options, path = seq.input_args(24)
        self.assertTrue(path.endswith("/f%d.jpg"))

    def test_un_fotograma_trae_la_secuencia_entera(self):
        files = self.touch("a_001.png", "a_002.png", "a_003.png")
        seq, = sequence.from_selection(files[1:2])
        self.assertEqual(seq.frames, (1, 2, 3))
        seq, = sequence.from_selection(files[:2])       # varios: solo esos
        self.assertEqual(seq.frames, (1, 2))

    def test_patron_sin_prefijo_y_con_porcentajes(self):
        seq, = sequence.find(self.touch("0001.exr", "0002.exr"))
        options, path = seq.input_args(23.976)
        self.assertEqual(path, f"{self.dir}/%04d.exr")
        self.assertEqual(value(options, "-framerate"), "24000/1001")
        self.assertEqual(value(options, "-start_number"), "1")
        seq, = sequence.find(self.touch("50%_01.png", "50%_02.png"))
        self.assertTrue(seq.input_args(24)[1].endswith("/50%%_%02d.png"))

    def test_huecos_con_enlaces_seguidos(self):
        seq, = sequence.find(self.touch("x[1]_8.png", "x[1]_9.png", "x[1]_12.png"))   # sin ceros
        self.assertEqual(sequence.describe_missing(seq.missing()), "10-11")
        links = seq.link_frames(self.dir / "enlaces 100%")
        self.assertEqual(sorted(p.name for p in links.iterdir()), ["000001.png", "000002.png", "000003.png"])
        self.assertEqual((links / "000003.png").resolve(), (self.dir / "x[1]_12.png").resolve())
        options, path = seq.input_args(24, links)
        self.assertEqual(path, f"{self.dir}/enlaces 100%%/%06d.png")
        self.assertEqual(value(options, "-start_number"), "1")

    def test_carpetas_traen_sus_secuencias(self):
        self.touch("v.mp4", "r_0001.png", "r_0002.png", "portada.jpg")
        found = expand_paths([self.dir])
        self.assertEqual([Path(str(f)).name for f in found], ["v.mp4", "r_####.png"])

    def test_cambiar_fps_cambia_la_duracion(self):
        seq, = sequence.find(self.touch("r_0001.png", "r_0002.png", "r_0003.png", "r_0004.png"))
        frame = media(w=320, h=240, fps=25, pix="rgb24", vcodec="png", audio=())
        info = sequence_info(seq, frame, 24)
        self.assertEqual((info.frames, info.duration, info.video.fps), (4, 4 / 24, 24))
        info = sequence_info(seq, info, 2)
        self.assertEqual(info.duration, 2)
        job = Job(source=seq, preset=P["mp4_h264"])
        job.set_probed(info)
        job.set_fps(4)
        self.assertEqual(job.info.duration, 1)

    def test_una_sola_imagen_no_es_un_video(self):
        seq, = sequence.find(self.touch("foto.png"), min_frames=1)
        info = sequence_info(seq, media(vcodec="png", audio=()), 24)
        self.assertIn("una sola imagen", command.check(info, P["mp4_h264"]))
        self.assertEqual(seq.frame_path(seq.first), self.dir / "foto.png")
        self.assertEqual(len(sequence.from_selection([self.dir / "foto.png"])), 1)

    def test_exr_pasa_de_lineal_a_srgb(self):
        info = media(pix="gbrapf32le", vcodec="exr", audio=())
        self.assertIn("zscale=transferin=linear:transfer=iec61966-2-1,format=gbrapf32le",
                      value(args_for(info, P["mp4_h264"]), "-vf"))


class ImageOutputTests(unittest.TestCase):
    def test_png_jpeg_tiff(self):
        a = command.build(media(), P["img_png"], "/o/v_%04d.png")
        self.assertEqual((value(a, "-c:v"), value(a, "-pix_fmt"), value(a, "-f")), ("png", "rgb24", "image2"))
        self.assertIn("-an", a)
        a = command.build(media(depth=10, pix="yuv420p10le"), P["img_tiff"], "/o/v_%04d.tif")
        self.assertEqual(value(a, "-pix_fmt"), "rgb48le")
        a = command.build(media(pix="rgba", vcodec="png"), P["img_png"], "/o/v_%04d.png")
        self.assertEqual(value(a, "-pix_fmt"), "rgba")      # conserva la transparencia
        a = command.build(media(), P["img_jpeg"], "/o/v_%04d.jpg")
        self.assertEqual((value(a, "-c:v"), value(a, "-q:v")), ("mjpeg", "3"))

    def test_una_por_segundo(self):
        a = command.build(media(), P["img_jpeg_1s"], "/o/v_%04d.jpg")
        self.assertIn("fps=1:round=up", value(a, "-vf"))

    def test_carpeta_y_patron(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "clip.mp4"
            src.write_bytes(b"")
            (Path(d) / "clip").mkdir()
            final = naming.output_path(src, "", None)
            self.assertEqual(final.name, "clip (2)")
            self.assertEqual(naming.frame_pattern(Path(d) / "tmp%", final, "png", 5),
                             f"{d}/tmp%%/clip (2)_%05d.png")
        self.assertEqual(command.frame_digits(media(duration=60, fps=25), P["img_png"]), 4)
        self.assertEqual(command.frame_digits(media(duration=3600, fps=60), P["img_png"]), 6)


if __name__ == "__main__":
    unittest.main()
