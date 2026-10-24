"""Convierte de verdad, con ffmpeg, cada perfil de fábrica sobre ficheros difíciles.

Lento (~1-2 min). Es la prueba que asegura que ningún perfil genera un comando roto.
    python3 -m unittest tests.test_real_conversions
"""

import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from konvi.core import command, encoders, presets, probe

EXPECTED_VIDEO = {"h264": "h264", "h265": "hevc", "av1": "av1", "vp9": "vp9", "prores": "prores",
                  "dnxhr": "dnxhd", "ffv1": "ffv1", "mpeg2": "mpeg2video"}
EXPECTED_AUDIO = {"aac": "aac", "opus": "opus", "mp3": "mp3", "flac": "flac", "pcm": "pcm_s24le",
                  "ac3": "ac3", "mp2": "mp2"}


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,width,height,channels:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    import json
    return json.loads(out.stdout)


class RealConversions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.dir = Path(cls.tmp.name)
        lavfi_v = ["-f", "lavfi", "-i"]
        # normal: 720p 25 fps, AAC estéreo
        ff(*lavfi_v, "testsrc2=size=1280x720:rate=25:duration=2", *lavfi_v, "sine=duration=2",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "normal.mp4"))
        # difícil: tamaño impar, 50 fps, audio 5.1 y subtítulos mov_text
        (d / "subs.srt").write_text("1\n00:00:00,000 --> 00:00:01,500\nHola\n")
        ff(*lavfi_v, "testsrc2=size=321x241:rate=50:duration=2",
           *lavfi_v, "sine=duration=2,aformat=channel_layouts=5.1(side)",
           "-i", str(d / "subs.srt"), "-map", "0", "-map", "1", "-map", "2",
           "-c:v", "libx264", "-pix_fmt", "yuv444p", "-c:a", "aac", "-c:s", "mov_text",
           "-shortest", str(d / "raro.mp4"))
        # entrelazado (MPEG-2 con campo superior primero)
        ff(*lavfi_v, "testsrc2=size=720x576:rate=25:duration=2,setfield=tff", *lavfi_v, "sine=duration=2",
           "-c:v", "mpeg2video", "-flags", "+ilme+ildct", "-c:a", "mp2",
           "-shortest", str(d / "entrelazado.mpg"))
        # H.265 de 10 bits, sin audio
        ff(*lavfi_v, "testsrc2=size=1920x1080:rate=24:duration=1", "-c:v", "libx265",
           "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error", str(d / "diez_bits.mkv"))
        # móvil en vertical: grabado apaisado con rotación de 90º en los metadatos
        ff("-display_rotation", "90", "-i", str(d / "normal.mp4"), "-c", "copy", str(d / "movil.mp4"))
        # solo audio
        ff(*lavfi_v, "sine=duration=2", "-c:a", "libmp3lame", str(d / "audio.mp3"))
        cls.infos = {p.stem: probe.probe(p) for p in d.iterdir() if p.suffix != ".srt"}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def convert(self, source, preset, hardware=command.NO_HARDWARE, use_hw=False):
        info = self.infos[source]
        out = self.dir / "out" / f"{source}-{preset.id}{'-hw' if use_hw else ''}.{preset.container}"
        out.parent.mkdir(exist_ok=True)
        args = command.build(info, preset, out, hardware, use_hw)
        r = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"{preset.id} con {source}:\n{' '.join(args)}\n{r.stderr[-800:]}")
        data = streams(out)
        kinds = {}
        for s in data["streams"]:
            kinds.setdefault(s["codec_type"], []).append(s)
        if preset.video:
            self.assertEqual(kinds["video"][0]["codec_name"], EXPECTED_VIDEO[preset.video], preset.id)
        else:
            self.assertNotIn("video", kinds, preset.id)
        if preset.audio and info.audio:
            names = {s["codec_name"] for s in kinds["audio"]}
            expected = {info.audio[0].codec}
            if preset.audio != "copy":
                expected.add(EXPECTED_AUDIO[preset.audio])
            self.assertTrue(names & expected, (preset.id, names))
        if info.duration:
            self.assertAlmostEqual(float(data["format"]["duration"]), info.duration, delta=0.6)
        return data, kinds

    def test_todos_los_perfiles_con_un_video_normal(self):
        for p in presets.BUILTIN:
            with self.subTest(p.id):
                self.convert("normal", p)

    def test_todos_los_perfiles_de_video_con_un_video_raro(self):
        for p in presets.BUILTIN:
            if p.video:
                with self.subTest(p.id):
                    _, kinds = self.convert("raro", p)
                    v = kinds["video"][0]
                    if p.profile != "dvd" and p.video != "ffv1":
                        self.assertEqual((v["width"] % 2, v["height"] % 2), (0, 0), p.id)

    def test_mkv_conserva_subtitulos_y_el_5_1(self):
        _, kinds = self.convert("raro", presets.builtin_by_id()["mkv_h265"])
        self.assertIn("subtitle", kinds)
        self.assertEqual(kinds["audio"][0]["channels"], 6)

    def test_opus_con_5_1_side(self):
        _, kinds = self.convert("raro", presets.builtin_by_id()["webm_av1"])
        self.assertEqual(kinds["audio"][0]["channels"], 6)

    def test_tele_estereo_mas_envolvente(self):
        _, kinds = self.convert("raro", presets.builtin_by_id()["device_tv"])
        self.assertEqual([s["channels"] for s in kinds["audio"]], [2, 6])

    def test_whatsapp_baja_a_estereo(self):
        _, kinds = self.convert("raro", presets.builtin_by_id()["web_whatsapp"])
        self.assertEqual(kinds["audio"][0]["channels"], 2)

    def test_entrelazado_10_bits_y_movil(self):
        for source in ("entrelazado", "diez_bits", "movil"):
            for pid in ("mp4_h264", "mp4_h265", "edit_dnxhr_hq", "mpeg_dvd", "web_youtube"):
                with self.subTest(f"{source}/{pid}"):
                    self.convert(source, presets.builtin_by_id()[pid])

    def test_movil_sigue_en_vertical_y_1080p_limita_el_lado_corto(self):
        _, kinds = self.convert("movil", presets.builtin_by_id()["mp4_h264"])
        v = kinds["video"][0]
        self.assertEqual((v["width"], v["height"]), (720, 1280))
        _, kinds = self.convert("movil", presets.builtin_by_id()["mp4_h264_480"])
        v = kinds["video"][0]
        self.assertEqual(v["width"], 480)

    def test_nunca_agranda(self):
        _, kinds = self.convert("raro", presets.builtin_by_id()["device_tv"])
        self.assertLessEqual(kinds["video"][0]["height"], 241)

    def test_solo_audio(self):
        for p in presets.BUILTIN:
            if p.is_audio_only:
                with self.subTest(p.id):
                    self.convert("audio", p)

    def test_un_perfil_de_video_con_un_mp3_da_un_error_claro(self):
        with self.assertRaises(command.ConvertError) as e:
            command.build(self.infos["audio"], presets.builtin_by_id()["mp4_h264"], self.dir / "x.mp4")
        self.assertIn("Solo audio", str(e.exception))

    def test_tamano_objetivo(self):
        p = replace(presets.builtin_by_id()["web_discord"], target_mb=0.5)
        self.convert("normal", p)
        size = (self.dir / "out" / f"normal-{p.id}.mp4").stat().st_size
        self.assertLess(size, 0.5 * 1024 * 1024 * 1.1)

    def test_sin_perdida_es_identico(self):
        p = presets.builtin_by_id()["mp4_lossless"]
        png = self.dir / "fotograma.png"
        ff("-i", str(self.dir / "raro.mp4"), "-frames:v", "1", str(png))
        self.infos["fotograma"] = probe.probe(png)       # RGB y de tamaño impar
        for source, path in (("normal", "normal.mp4"), ("diez_bits", "diez_bits.mkv"),
                             ("fotograma", "fotograma.png")):
            with self.subTest(source):
                self.convert(source, p)
                out = self.dir / "out" / f"{source}-{p.id}.mp4"
                pix = self.infos[source].video.pix_fmt
                frame = lambda f: subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", str(f), "-frames:v", "1", "-f", "rawvideo",
                     "-pix_fmt", pix, "-"], capture_output=True, check=True).stdout
                self.assertEqual(frame(out), frame(self.dir / path), source)

    def test_tarjeta_grafica(self):
        hw = encoders.detect()
        if not hw.any:
            self.skipTest("este equipo no tiene codificación por GPU")
        for pid in ("mp4_h264", "mp4_h265", "web_youtube", "web_discord", "device_tv"):
            for source in ("normal", "raro", "diez_bits"):
                with self.subTest(f"{source}/{pid}"):
                    p = presets.builtin_by_id()[pid]
                    args = command.build(self.infos[source], p, self.dir / "x.mp4", hw, True)
                    self.assertTrue(command.uses_hardware(args))
                    self.convert(source, p, hw, True)


if __name__ == "__main__":
    unittest.main()
