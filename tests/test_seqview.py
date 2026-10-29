"""El visor de secuencias integrado (seqview), sin pantalla:  python3 -m unittest tests.test_seqview"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_config = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _config.name       # que los ajustes guardados no toquen los de verdad
os.environ.setdefault("KONVI_GPU", '"cpu"')        # numpy: las pruebas de la gráfica van aparte (test_gpu)

from PySide6.QtCore import QCoreApplication, QElapsedTimer, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import numpy as np  # noqa: E402

from konvi import i18n  # noqa: E402
from konvi.ui import seqview  # noqa: E402

app = QApplication.instance() or QApplication([])


def wait_until(cond, ms=10000):
    t = QElapsedTimer()
    t.start()
    while not cond() and t.elapsed() < ms:
        QCoreApplication.processEvents()
    return cond()


class SeqviewTests(unittest.TestCase):
    def setUp(self):
        i18n.set_language("es")              # las comprobaciones de texto están en español
        # cada prueba abre sin los efectos que haya guardado otra (ahora se abren activos)
        prefs = seqview.QSettings("konvi", "konvi")
        prefs.remove("seqview/look")
        prefs.remove("seqview/look_muted")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc=size=320x240:rate=24:duration=1", "-start_number", "1",
                        str(cls.dir / "shot_%04d.png")], check=True)
        for name in ("otra_0001.jpg", "otra_0002.jpg", "notas.txt"):
            (cls.dir / name).write_bytes(b"x")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_una_imagen_abre_toda_su_secuencia(self):
        paths = seqview.collect([str(self.dir / "shot_0010.png")])
        self.assertEqual(len(paths), 24)
        self.assertEqual(paths[0].name, "shot_0001.png")
        self.assertEqual(seqview.describe(paths), "shot_####.png  [1-24]")

    def test_carpeta_elige_la_secuencia_mas_larga(self):
        paths = seqview.collect([str(self.dir)])
        self.assertEqual(len(paths), 24)
        self.assertTrue(all(p.suffix == ".png" for p in paths))

    def test_seleccion_en_orden_natural(self):
        files = [str(self.dir / f"shot_{i:04d}.png") for i in (10, 2, 1)]
        self.assertEqual([p.name for p in seqview.collect(files)],
                         ["shot_0001.png", "shot_0002.png", "shot_0010.png"])

    def test_carga_reproduce_y_da_la_vuelta(self):
        player = seqview.Player(seqview.collect([str(self.dir)]), fps=200)
        try:
            self.assertTrue(wait_until(lambda: len(player.cache.images) == 24))
            self.assertEqual(player.viewer.image.width(), 320)
            player.toggle_play()
            self.assertTrue(wait_until(lambda: player.current == 23))
            self.assertTrue(wait_until(lambda: player.current < 23))   # bucle
            player.pause()
            player.step(-1)
            self.assertFalse(player.timer.isActive())
        finally:
            player.close()
            player.cache.wait()

    def test_sin_bucle_se_para_al_final(self):
        player = seqview.Player(seqview.collect([str(self.dir)]), fps=200)
        try:
            player.btn_loop.setChecked(False)
            self.assertTrue(wait_until(lambda: len(player.cache.images) == 24))
            player.toggle_play()
            self.assertTrue(wait_until(lambda: not player.timer.isActive()))
            self.assertEqual(player.current, 23)
        finally:
            player.close()
            player.cache.wait()

    def test_fotograma_roto_no_bloquea_la_reproduccion(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for i in range(1, 6):
                (d / f"f_{i:03d}.png").write_bytes((self.dir / f"shot_{i:04d}.png").read_bytes())
            (d / "f_003.png").write_bytes(b"roto")
            player = seqview.Player(seqview.collect([str(d)]), fps=200)
            try:
                player.btn_loop.setChecked(False)
                self.assertTrue(wait_until(lambda: len(player.cache.images) + len(player.cache.errors) == 5))
                self.assertIn(2, player.cache.errors)
                player.toggle_play()
                self.assertTrue(wait_until(lambda: player.current == 4))
            finally:
                player.close()
                player.cache.wait()

    def test_arrastrar_sobre_la_imagen_recorre_la_secuencia(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        player = seqview.Player(seqview.collect([str(self.dir)]), fps=200)
        try:
            player.resize(480, 400)
            player.show()
            self.assertTrue(wait_until(lambda: len(player.cache.images) == 24))
            v = player.viewer
            px = max(2.0, min(10.0, v.width() / 24))
            player.seek(10)
            QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(200, 100))
            QTest.mouseMove(v, QPoint(200 + int(3 * px), 100))
            self.assertEqual(player.current, 13)                 # hacia delante
            QTest.mouseMove(v, QPoint(200 - int(5 * px), 100))
            self.assertEqual(player.current, 5)                  # hacia atrás
            QTest.mouseMove(v, QPoint(0, 100))
            self.assertGreaterEqual(player.current, 0)           # no se sale
            QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(0, 100))
            pan = QPoint(int(v.pan.x()), int(v.pan.y()))
            self.assertEqual(pan, QPoint(0, 0))                  # el izquierdo ya no mueve la imagen
        finally:
            player.close()
            player.cache.wait()

    def test_al_soltar_el_arrastre_sigue_reproduciendo(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        player = seqview.Player(seqview.collect([str(self.dir)]), fps=200)
        try:
            player.resize(480, 400)
            player.show()
            self.assertTrue(wait_until(lambda: len(player.cache.images) == 24))
            v = player.viewer
            player.toggle_play()
            QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(200, 100))
            self.assertFalse(player.timer.isActive())            # mientras se arrastra, quieto
            QTest.mouseMove(v, QPoint(150, 100))
            QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(150, 100))
            self.assertTrue(player.timer.isActive())             # al soltar, sigue
            player.pause()
            QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(200, 100))
            QTest.mouseMove(v, QPoint(150, 100))
            QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(150, 100))
            self.assertFalse(player.timer.isActive())            # en pausa, se queda en pausa
            player.toggle_play()                                 # y en la línea de tiempo, igual
            QTest.mousePress(player.timeline, Qt.LeftButton, Qt.NoModifier, QPoint(10, 5))
            self.assertFalse(player.timer.isActive())
            QTest.mouseRelease(player.timeline, Qt.LeftButton, Qt.NoModifier, QPoint(10, 5))
            self.assertTrue(player.timer.isActive())
        finally:
            player.close()
            player.cache.wait()

    def test_clic_quieto_en_la_imagen_pausa_y_reproduce(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        player = seqview.Player(seqview.collect([str(self.dir)]), fps=200)
        try:
            player.resize(480, 400)
            player.show()
            self.assertTrue(wait_until(lambda: len(player.cache.images) == 24))
            v = player.viewer
            player.seek(10)
            QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, QPoint(200, 100))
            self.assertTrue(player.timer.isActive())             # en pausa, un clic reproduce
            QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, QPoint(200, 100))
            self.assertFalse(player.timer.isActive())            # reproduciendo, un clic pausa
            frame = player.current
            QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, QPoint(200, 100))
            QTest.mouseMove(v, QPoint(201, 100))                 # temblor de la mano: sigue siendo clic
            QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, QPoint(201, 100))
            self.assertTrue(player.timer.isActive())
            player.pause()
            self.assertGreaterEqual(player.current, frame)
        finally:
            player.close()
            player.cache.wait()

    def test_abre_la_ventana_antes_y_ensena_la_carga(self):
        player = seqview.Player([], fps=24)
        try:
            player.show()
            player.open_async([str(self.dir / "shot_0005.png")])
            self.assertEqual(player.viewer.message, "Buscando la secuencia…")   # sin bloquear
            seen = []
            while len(player.cache.images) < 24 and wait_until(lambda: True, 1):
                QCoreApplication.processEvents()
                if player.load_bar.isVisible():
                    seen.append(player.load_bar.value())
            self.assertTrue(wait_until(lambda: len(player.cache.images) == 24))
            self.assertTrue(seen, "la barra de carga no llegó a verse")
            self.assertFalse(player.load_bar.isVisible())                      # al terminar, fuera
            player.open_async([str(self.dir / "no_existe")])
            self.assertTrue(wait_until(lambda: "No hay imágenes" in player.viewer.message))
        finally:
            player.close()
            player.cache.wait()

    def test_se_reproduce_solo_al_abrir(self):
        player = seqview.Player([], fps=200, autoplay=True)
        try:
            player.open_async([str(self.dir)])
            self.assertTrue(wait_until(lambda: player.timer.isActive()))          # sin pulsar Play
            self.assertTrue(wait_until(lambda: player.current > 5))
            quiet = seqview.Player(seqview.collect([str(self.dir)]), fps=200)      # sin autoplay
            self.assertFalse(quiet.timer.isActive())
            quiet.close()
            quiet.cache.wait()
        finally:
            player.close()
            player.cache.wait()

    def test_lo_que_qt_no_lee_lo_lee_ffmpeg(self):
        with tempfile.TemporaryDirectory() as d:
            exr = Path(d) / "gris_0001.exr"              # lineal: 0,2157 es el gris medio en sRGB
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x373737:size=32x32",
                            "-frames:v", "1", "-c:v", "exr", "-pix_fmt", "gbrpf32le", str(exr)], check=True)
            image, error = seqview._ffmpeg_exr(exr)
            self.assertEqual(error, "")
            self.assertTrue(seqview.is_hdr(image))               # en lineal y sin recortar
            self.assertAlmostEqual(seqview.develop(image).pixelColor(5, 5).red(), 128, delta=2)
            image, error = seqview.load_image(exr)
            self.assertFalse(image.isNull(), error)
            self.assertAlmostEqual(seqview.develop(image).pixelColor(5, 5).red(), 128, delta=3)
            (Path(d) / "roto.png").write_bytes(b"roto")
            image, error = seqview.load_image(Path(d) / "roto.png")
            self.assertTrue(image.isNull())
            self.assertTrue(error)

    def test_exposicion_saca_las_luces_de_un_exr(self):
        from PySide6.QtGui import QImage
        image = QImage(4, 1, QImage.Format_RGBA16FPx4)
        for x, value in enumerate((0.0, 0.2159, 1.0, 4.0)):     # 0,2159 lineal = 128 en sRGB
            image.setPixelColor(x, 0, QColor.fromRgbF(value, value / 2, 0.0, 1.0))
        shown = seqview.develop(image)
        self.assertEqual([shown.pixelColor(x, 0).red() for x in range(4)], [0, 128, 255, 255])
        shown = seqview.develop(image, exposure=-2)              # 4,0 baja a 1,0: ya no se quema
        self.assertEqual(shown.pixelColor(3, 0).red(), 255)
        self.assertLess(shown.pixelColor(2, 0).red(), 255)
        shown = seqview.develop(image, gamma=2.0)
        self.assertGreater(shown.pixelColor(1, 0).red(), 128)
        shown = seqview.develop(image, channel="g")              # el verde, en gris
        c = shown.pixelColor(3, 0)
        self.assertEqual((c.red(), c.green(), c.blue()), (255, 255, 255))
        c = shown.pixelColor(1, 0)
        self.assertEqual(c.red(), c.blue())
        self.assertFalse(shown.hasAlphaChannel())

    def test_teclas_de_canal_exposicion_y_gamma(self):
        from PySide6.QtTest import QTest
        player = seqview.Player(seqview.collect([str(self.dir)]), fps=200)
        try:
            player.show()
            self.assertTrue(wait_until(lambda: len(player.cache.images) == 24))
            v = player.viewer
            frame = v.image
            self.assertIs(seqview.develop(frame), frame)          # 8 bits sin tocar: tal cual
            for key, channel in ((Qt.Key_R, "r"), (Qt.Key_G, "g"), (Qt.Key_B, "b"), (Qt.Key_C, "rgb")):
                QTest.keyClick(player, key)
                self.assertEqual(v.channel, channel)
            QTest.keyClick(player, Qt.Key_R)
            QTest.keyClick(player, Qt.Key_R)                     # la misma otra vez: al color
            self.assertEqual(v.channel, "rgb")
            QTest.keyClick(player, Qt.Key_A, Qt.ShiftModifier)
            self.assertEqual(v.channel, "a")
            self.assertFalse(v.checker)                          # la A sola sigue siendo el fondo
            QTest.keyClick(player, Qt.Key_E)
            QTest.keyClick(player, Qt.Key_E)
            QTest.keyClick(player, Qt.Key_E, Qt.ShiftModifier)
            self.assertEqual(v.exposure, seqview.EXPOSURE_STEP)
            QTest.keyClick(player, Qt.Key_Y)
            self.assertAlmostEqual(v.gamma, 1 + seqview.GAMMA_STEP)
            QTest.keyClick(player, Qt.Key_C)
            v.grab()                                             # se pinta revelado sin fallar
            self.assertIsNot(v.displayed(), frame)
            QTest.keyClick(player, Qt.Key_0)
            self.assertEqual((v.exposure, v.gamma), (0.0, 1.0))
            self.assertIs(v.displayed(), frame)
        finally:
            player.close()
            player.cache.wait()

    def test_mayus_flechas_saltan_al_principio_y_al_final(self):
        from PySide6.QtTest import QTest
        player = seqview.Player(seqview.collect([str(self.dir)]), fps=200)
        try:
            player.seek(10)
            QTest.keyClick(player, Qt.Key_Right)
            self.assertEqual(player.current, 11)
            QTest.keyClick(player, Qt.Key_Right, Qt.ShiftModifier)       # como en Blender
            self.assertEqual(player.current, len(player.paths) - 1)
            QTest.keyClick(player, Qt.Key_Left, Qt.ShiftModifier)
            self.assertEqual(player.current, 0)
            # también con el foco en una casilla numérica, que las usaría para seleccionar texto
            player.seek(10)
            for box in (player.exposure_box, player.panel.rows[("grain", "amount")].spin):
                QTest.keyClick(box, Qt.Key_Right, Qt.ShiftModifier)
                self.assertEqual(player.current, len(player.paths) - 1)
                QTest.keyClick(box, Qt.Key_Left, Qt.ShiftModifier)
                self.assertEqual(player.current, 0)
            QTest.keyClick(player.exposure_box, Qt.Key_Right)              # sin Mayús, sigue siendo de la casilla
            self.assertEqual(player.current, 0)
        finally:
            player.close()
            player.cache.wait()

    def test_refrescar_anade_los_fotogramas_nuevos(self):
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            copy = lambda i: (d / f"r_{i:03d}.png").write_bytes((self.dir / f"shot_{i:04d}.png").read_bytes())
            for i in range(1, 6):
                copy(i)
            # como desde la lista de Konvi: los fotogramas exactos, sin buscar
            player = seqview.Player([d / f"r_{i:03d}.png" for i in range(1, 6)], fps=24)
            try:
                self.assertTrue(wait_until(lambda: len(player.cache.images) == 5))
                player.refresh()
                self.assertTrue(wait_until(lambda: player._notice == "No hay fotogramas nuevos"))
                player.seek(3)
                kept = player.cache.images[0]
                for i in range(6, 9):           # el render sigue y deja tres más
                    copy(i)
                QTest.keyClick(player, Qt.Key_R, Qt.ControlModifier)
                self.assertTrue(wait_until(lambda: len(player.paths) == 8))
                self.assertEqual(player._notice, "3 fotogramas nuevos")
                self.assertEqual(player.current, 3)                    # el cabezal no se mueve
                self.assertIs(player.cache.images.get(0), kept)        # lo ya leído no se relee
                self.assertTrue(wait_until(lambda: len(player.cache.images) == 8))
                self.assertIn("[1-8]", player.windowTitle())
            finally:
                player.close()
                player.cache.wait()

    def test_refrescar_relee_los_fotogramas_cambiados(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            copy = lambda i, j=None: (d / f"r_{i:03d}.png").write_bytes(
                (self.dir / f"shot_{j or i:04d}.png").read_bytes())
            for i in range(1, 6):
                copy(i)
            player = seqview.Player([d / f"r_{i:03d}.png" for i in range(1, 6)], fps=24)
            try:
                self.assertTrue(wait_until(lambda: len(player.cache.images) == 5))
                player.seek(1)
                kept, old = player.cache.images[0], player.cache.images[1]
                copy(2, 9)                      # se vuelve a renderizar el fotograma 2
                st = os.stat(d / "r_002.png")
                os.utime(d / "r_002.png", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
                player.refresh()
                self.assertTrue(wait_until(lambda: player._notice == "1 fotograma cambiado"))
                self.assertEqual(len(player.paths), 5)
                self.assertEqual(player.current, 1)
                self.assertIs(player.cache.images.get(0), kept)        # lo que no cambió no se relee
                self.assertTrue(wait_until(lambda: 1 in player.cache.images))
                self.assertIsNot(player.cache.images[1], old)
                self.assertIs(player.viewer.image, player.cache.images[1])  # y se ve el nuevo
                player.refresh()
                self.assertTrue(wait_until(lambda: player._notice == "No hay fotogramas nuevos"))
            finally:
                player.close()
                player.cache.wait()

    def test_cache_limitada_por_memoria(self):
        cache = seqview.FrameCache()
        cache.set_paths([Path(f"x{i}.png") for i in range(1000)])
        cache.frame_bytes = 100 << 20
        cache.budget = 1 << 30            # caben 10
        order = cache.wanted(995, loop=True)
        self.assertEqual(len(order), 10)
        self.assertEqual(order[:6], [995, 996, 997, 998, 999, 0])   # da la vuelta
        self.assertIn(994, order)                                     # y guarda alguno de atrás
        # sin bucle, al final: lo que sobra delante se usa hacia atrás
        order = cache.wanted(998, loop=False)
        self.assertEqual(order, [998, 999, 997, 996, 995, 994, 993, 992, 991, 990])
        cache.set_paths([Path(f"x{i}.png") for i in range(48)])
        cache.frame_bytes = 8 << 20
        self.assertEqual(len(cache.wanted(47, loop=False)), 48)

    def test_revelados_se_reutilizan_hasta_que_cambia_un_ajuste(self):
        from PySide6.QtGui import QImage
        cache = seqview.FrameCache()
        cache.set_paths([Path(f"x{i}.png") for i in range(3)])
        frame = QImage(64, 32, QImage.Format_RGB32)
        frame.fill(QColor("gray"))
        calls = []

        def make():
            calls.append(1)
            return frame.copy()

        a = cache.developed_image(0, frame, ("look", 1), make)
        self.assertIs(cache.developed_image(0, frame, ("look", 1), make), a)    # de memoria
        self.assertEqual(len(calls), 1)
        cache.developed_image(0, frame, ("look", 2), make)                      # otro ajuste
        self.assertEqual(len(calls), 2)
        self.assertEqual(list(cache.developed), [0])
        other = frame.copy()                                     # el fichero se volvió a leer
        cache.developed_image(0, other, ("look", 2), make)
        self.assertEqual(len(calls), 3)
        self.assertIs(cache.developed_image(1, frame, ("look", 2), lambda: frame), frame)
        self.assertNotIn(1, cache.developed)                    # sin revelar no se duplica
        cache.budget = frame.sizeInBytes() * 3                  # no cabe la secuencia dos veces
        cache.developed_image(2, frame, ("look", 2), make)
        self.assertEqual(cache.developed, {})

    def test_silenciar_no_tira_lo_revelado(self):
        from PySide6.QtGui import QImage
        cache = seqview.FrameCache()
        cache.set_paths([Path(f"x{i}.png") for i in range(2)])
        viewer = seqview.Viewer()
        viewer.store = cache
        settings = seqview.fx.default_settings()
        settings["vignette"]["on"] = True
        viewer.look.set(settings)
        frame = QImage(64, 32, QImage.Format_RGB32)
        frame.fill(QColor("gray"))
        viewer.index = 0
        viewer.set_image(frame)
        developed = viewer.displayed()
        self.assertIn(0, cache.developed)
        viewer.bypass = True
        self.assertIs(viewer.displayed(), frame)                   # silenciado: el original
        viewer.bypass = False
        self.assertIn(0, cache.developed)                          # lo revelado sigue ahí
        self.assertIs(viewer.displayed(), developed)




class LookTests(unittest.TestCase):
    """Los efectos de cámara: que las cifras sean las que dicen los ajustes."""

    fx = seqview.fx

    def test_vineteado_da_la_cantidad_en_la_esquina(self):
        g = self.fx.vignette_gain(400, 300, -1.3)
        self.assertAlmostEqual(float(g[150, 200]), 1.0, places=3)                # centro
        corner = float(g[0, 0])
        self.assertAlmostEqual(np.log2(corner), -1.3, delta=0.02)              # esquina
        # redondo de verdad: el centro del lado izquierdo (más lejos) más oscuro que el de arriba
        self.assertLess(float(g[150, 0]), float(g[0, 200]))
        # cuadrada y ajustada al encuadre: un rectángulo de esquinas redondeadas, que oscurece
        # igual el centro de cada lado y deja el centro de la imagen más amplio
        square = self.fx.vignette_gain(400, 300, -1.3, roundness=1.0, fit=1.0)
        self.assertAlmostEqual(np.log2(float(square[0, 0])), -1.3, delta=0.02)
        self.assertAlmostEqual(float(square[0, 200]), float(square[150, 0]), delta=0.01)
        self.assertGreater(float(square[75, 100]), float(g[75, 100]))

    def test_distorsion_da_la_tv_distortion_pedida(self):
        for tv in (-12.0, -1.0, 3.0):
            lam = self.fx.distortion_lambda(tv, 1920, 1080)
            self.assertAlmostEqual(self.fx._tv_of(lam, 1920, 1080), tv, places=3)
        self.assertLess(self.fx.distortion_lambda(-1, 1920, 1080), 0)          # barril

    def test_rellenar_el_encuadre_no_deja_esquinas_vacias(self):
        img = np.ones((90, 160, 4), np.float32)
        for tv, ca in ((-12, 0), (0, 0.5), (-3, 0.25), (5, 0.1)):
            settings = self.fx.default_settings()
            settings["distortion"].update(on=bool(tv), amount=tv, fill=True)
            settings["aberration"].update(on=bool(ca), amount=ca)
            out = self.fx.Look(settings).apply(img.copy())
            self.assertGreater(out.min(), 0.99, (tv, ca))

    def test_aberracion_separa_rojo_y_azul_en_los_bordes(self):
        img = np.zeros((101, 201, 4), np.float32)
        img[..., 3] = 1
        img[:, 150:160, :3] = 1                         # una raya blanca a la derecha
        settings = self.fx.default_settings()
        settings["aberration"].update(on=True, amount=2.0)
        settings["distortion"]["fill"] = False
        out = self.fx.Look(settings).apply(img)
        row = out[50]
        center = lambda c: float((row[:, c] * np.arange(201)).sum() / row[:, c].sum())
        self.assertGreater(center(0), center(1))        # el rojo, más hacia fuera
        self.assertGreater(center(1), center(2))
        self.assertAlmostEqual(center(1), 154.5, delta=0.3)

    def test_grano_igual_en_el_mismo_fotograma_y_distinto_en_otro(self):
        settings = self.fx.default_settings()
        settings["grain"]["on"] = True
        look = self.fx.Look(settings)
        gray = np.full((64, 96, 4), 0.18, np.float32)
        a = look.apply(gray.copy(), 7)
        b = look.apply(gray.copy(), 7)
        c = look.apply(gray.copy(), 8)
        np.testing.assert_array_equal(a, b)
        self.assertFalse(np.array_equal(a, c))
        # película: σ en pasos como dice la ficha (sin ruido en el alfa)
        stops, _ = self.fx.film_grain_params("250D", "35", 96)
        measured = np.log2(a[..., :3] / 0.18).reshape(-1, 3).std(axis=0)
        np.testing.assert_allclose(measured, stops, rtol=0.15)
        self.assertTrue((a[..., 3] == 0.18).all())
        self.assertGreater(stops[2], stops[1])          # la capa azul, la de más grano

    def test_halacion_tine_de_rojo_alrededor_de_las_luces(self):
        img = np.zeros((90, 160, 4), np.float32)
        img[..., 3] = 1
        img[40:50, 75:85, :3] = 20.0                    # una luz fuerte (un EXR)
        img[10:14, 10:14, :3] = 0.5                     # algo por debajo del umbral
        settings = self.fx.default_settings()
        settings["halation"].update(on=True, amount=0.2, radius=0.5)
        out = self.fx.Look(settings).apply(img.copy())
        r, g, b = out[45, 88, :3]                       # junto a la luz (a un radio), en lo negro
        self.assertGreater(r, 0.01)
        self.assertGreater(r, g)
        self.assertGreater(g, b)
        np.testing.assert_allclose(out[12, 12, :3], 0.5, atol=1e-4)    # sin luces cerca, nada

    def test_glow_alrededor_de_las_luces(self):
        img = np.zeros((256, 256, 4), np.float32)
        img[..., 3] = 1
        img[126:130, 126:130, :3] = 50.0                # una luz muy fuerte (un EXR)
        img[20:24, 20:24, :3] = 0.5                     # algo por debajo del umbral
        glow = self.fx.glow_image(img[..., :3])
        self.assertGreater(glow[128, 150, 0], 0)        # el halo llega lejos de la luz
        self.assertGreater(glow[128, 140, 0], glow[128, 170, 0])     # y cae con la distancia
        self.assertLess(glow[22, 22].max(), glow[128, 150].max())
        long_tail = self.fx.glow_image(img[..., :3], falloff=1.2, size=1.0)
        short_tail = self.fx.glow_image(img[..., :3], falloff=3.5, size=1.0)
        far = lambda g: g[128, 200, 0] / g[128, 132, 0]
        self.assertGreater(far(long_tail), far(short_tail))           # cola larga: llega más lejos
        settings = self.fx.default_settings()
        settings["glow"].update(on=True, only=True, threshold=2.0)    # el 0,5 queda por debajo
        only = self.fx.Look(settings).apply(img.copy())
        self.assertLess(float(only[22, 22, :3].max()), 0.01)   # solo el glow: ni rastro del 0,5 de la imagen

    def test_mas_grano_en_el_pie_de_la_curva(self):
        settings = self.fx.default_settings()
        settings["grain"]["on"] = True
        look = self.fx.Look(settings)
        def stops(level):
            out = look.apply(np.full((64, 96, 4), level, np.float32), 3)
            return np.log2(out[..., 1] / level).std()
        self.assertGreater(stops(0.18 / 64), 1.5 * stops(0.18))       # 6 pasos por debajo del gris
        self.assertLess(stops(0.18 / 1024), 3.0 * stops(0.18))       # pero con límite

    def test_ruido_del_sensor_como_una_full_frame_real(self):
        # a 8 Mpx (lo que normaliza DxOMark): unos 45 dB en gris medio a ISO 100, 3 dB menos por paso
        w, h = 3464, 2309
        snr = lambda iso: 20 * np.log10(0.18 / np.sqrt(sum(
            np.array(self.fx.digital_noise_params(iso, w, h)) * (0.18, 1))))
        self.assertAlmostEqual(snr(100), 45, delta=0.2)
        self.assertAlmostEqual(snr(3200), 45 - 15.05, delta=0.2)    # DxOMark: A7 III ~32 dB a 3200
        settings = self.fx.default_settings()
        settings["grain"].update(on=True, kind="digital", iso=3200)
        look = self.fx.Look(settings)
        out = look.apply(np.full((128, 128, 4), 0.18, np.float32), 1)[..., :3]
        std = out.reshape(-1, 3).std(axis=0)
        self.assertGreater(std[2], std[1])                   # más ruido en el azul (balance de blancos)
        # la reducción de ruido de un móvil: menos ruido y en manchas más grandes
        settings["grain"]["nr"] = 1.0
        smooth = self.fx.Look(settings).apply(np.full((128, 128, 4), 0.18, np.float32), 1)[..., :3]
        self.assertLess(smooth.std(), out.std())
        corr = lambda a: np.corrcoef(a[:, :-1, 0].ravel(), a[:, 1:, 0].ravel())[0, 1]
        self.assertGreater(corr(smooth), corr(out))

    def test_balance_de_blancos_y_saturacion(self):
        white = np.ones((1, 1, 4), np.float32)
        warm = self.fx.grade(white.copy(), temperature=9000)[0, 0]
        cool = self.fx.grade(white.copy(), temperature=3200)[0, 0]
        self.assertGreater(warm[0], warm[2])                       # más de 6500 K: más cálido
        self.assertGreater(cool[2], cool[0])                       # bombilla compensada: más frío
        magenta = self.fx.grade(white.copy(), tint=50)[0, 0]
        self.assertLess(magenta[1], magenta[0])
        self.assertIsNone(self.fx.white_balance_matrix(6500, 0))   # por defecto, nada
        color = np.array([[[0.5, 0.2, 0.1, 1.0]]], np.float32)
        gray = self.fx.grade(color.copy(), saturation=0)[0, 0, :3]
        self.assertAlmostEqual(float(gray[0]), float(gray[2]), places=6)
        self.assertAlmostEqual(float(gray[0]), float(color[0, 0, :3] @ self.fx.LUMA), places=6)

    def test_brillo_contraste_y_gamma(self):
        px = np.array([[[0.0, 0.18, 1.0, 1.0]]], np.float32)
        same = self.fx.grade(px.copy())
        np.testing.assert_array_equal(same, px)
        np.testing.assert_allclose(self.fx.grade(px.copy(), exposure=1)[0, 0, :3], [0, 0.36, 2.0], rtol=1e-6)
        c = self.fx.grade(px.copy(), contrast=1.5)[0, 0, :3]
        self.assertEqual(c[0], 0)                                  # el negro sigue en negro
        self.assertAlmostEqual(float(c[1]), 0.18, places=5)        # el gris no se mueve
        self.assertGreater(c[2], 1.0)                              # las luces suben
        g = self.fx.grade(px.copy(), gamma=2.0)[0, 0, :3]
        self.assertAlmostEqual(float(g[2]), 1.0, places=6)         # el blanco no se mueve
        self.assertGreater(g[1], 0.18)                             # los medios se aclaran

    def test_mas_grano_en_super_8_que_en_35(self):
        s35, size35 = self.fx.film_grain_params("500T", "35", 1920)
        s8, size8 = self.fx.film_grain_params("500T", "8", 1920)
        self.assertTrue((s8 > s35).all())
        self.assertGreater(size8, size35)

    def test_presets_son_validos(self):
        for name in self.fx.PRESETS:
            s = self.fx.preset_settings(name)
            self.assertEqual(self.fx.matching_preset(s), name)
            self.assertEqual(set(s), set(self.fx.DEFAULTS))
            out = self.fx.Look(s).apply(np.full((36, 64, 4), 0.5, np.float32), 1)
            self.assertTrue(np.isfinite(out).all(), name)

    def test_develop_con_efectos(self):
        from PySide6.QtGui import QImage
        image = QImage(64, 36, QImage.Format_RGB32)
        image.fill(QColor(128, 128, 128))
        settings = self.fx.default_settings()
        settings["vignette"]["on"] = True
        shown = seqview.develop(image, look=self.fx.Look(settings))
        self.assertEqual(shown.size(), image.size())
        self.assertLess(shown.pixelColor(0, 0).red(), 100)
        self.assertAlmostEqual(shown.pixelColor(32, 18).red(), 128, delta=2)
        self.assertIs(seqview.develop(image, look=self.fx.Look()), image)   # sin efectos, tal cual


    def test_curvas_la_recta_no_cambia_nada(self):
        rng = np.random.default_rng(3)
        img = (rng.random((40, 60, 4)) * 3).astype(np.float32)       # con luces de EXR
        out = self.fx.apply_curves(img.copy(), self.fx.curves_table(self.fx.DEFAULTS["curves"]))
        np.testing.assert_allclose(out[..., :3], img[..., :3], rtol=1e-4, atol=1e-6)

    def test_curva_pasa_por_sus_puntos_sin_pasarse_de_largo(self):
        pts = [[0.0, 0.0], [0.3, 0.6], [0.35, 0.62], [1.0, 1.0]]
        np.testing.assert_allclose(self.fx.curve_eval(pts, [p[0] for p in pts]), [p[1] for p in pts], atol=1e-9)
        ys = self.fx.curve_eval(pts, np.linspace(0, 1, 501))
        self.assertTrue((np.diff(ys) >= -1e-12).all())             # monótona: no invierte tonos
        self.assertLessEqual(ys.max(), 1.0)
        self.assertAlmostEqual(float(self.fx.curve_eval([[0.2, 0.1], [0.8, 0.9]], 0.0)), 0.1)  # plana fuera

    def test_curvas_en_srgb_y_luces_por_la_pendiente(self):
        settings = self.fx.default_settings()
        c = settings["curves"]
        c["on"] = True
        c["master"] = [[0.0, 0.0], [0.5, 0.25], [1.0, 1.0]]      # oscurece los medios
        c["r"] = [[0.0, 0.0], [1.0, 0.5]]                        # el rojo, a la mitad (en sRGB)
        img = np.zeros((1, 3, 4), np.float32)
        img[0, 0, :3] = self.fx._decode(0.5)                     # el gris de un PNG a 128
        img[0, 1, :3] = 1.0
        img[0, 2, :3] = 4.0                                      # dos pasos sobre el blanco
        out = self.fx.Look(settings).apply(img.copy())
        enc = self.fx._encode(out[0])
        self.assertAlmostEqual(float(enc[0, 1]), 0.25, places=3)
        self.assertAlmostEqual(float(enc[0, 0]), 0.125, places=3)
        self.assertAlmostEqual(float(enc[1, 2]), 1.0, places=3)
        self.assertGreater(float(out[0, 2, 2]), 1.5)             # no se recorta en el blanco
        self.assertGreater(float(out[0, 2, 2]), float(out[0, 2, 0]))

    def test_comprimir_luces_deja_los_medios_y_llega_al_blanco(self):
        k, w, _ = self.fx.shoulder_params(1.5, 5.0)
        img = np.zeros((1, 6, 4), np.float32)
        img[0, :, 0] = [0.05, 0.18, k, w, 4 * w, 1000.0]
        out = self.fx.compress_highlights(img.copy(), 1.5, 5.0)[0, :, 0]
        np.testing.assert_allclose(out[:3], img[0, :3, 0], rtol=1e-6)         # por debajo, igual
        self.assertAlmostEqual(float(out[3]), 1.0, places=5)                # el blanco, en 1
        self.assertTrue((np.diff(out) > 0).all())                           # sigue subiendo
        self.assertLess(float(out[5]), 1.1)                                 # pero muy poco
        # sin codo en la rodilla: la pendiente justo encima es 1
        e = 1e-3
        x = np.array([[[k + e, 0, 0, 0]]], np.float32)
        self.assertAlmostEqual(float(self.fx.compress_highlights(x, 1.5, 5.0)[0, 0, 0] - k) / e, 1.0, delta=0.01)
        # rodilla o blanco imposibles (la rodilla por encima de 1): sigue llegando a 1 sin pasarse
        k, w, _ = self.fx.shoulder_params(4.0, 2.6)
        self.assertLess(k, 1)
        self.assertGreater(w, 1)

    def test_niveles_automaticos(self):
        rng = np.random.default_rng(5)
        img = np.ones((300, 400, 4), np.float32)
        enc = rng.uniform(0.2, 0.7, (300, 400, 3))
        enc[..., 2] = rng.uniform(0.3, 0.9, (300, 400))                     # con tinte azul
        img[..., :3] = self.fx._decode(enc)
        levels = self.fx.auto_levels(img)
        self.assertAlmostEqual(levels["r"][0][0], 0.2, delta=0.01)
        self.assertAlmostEqual(levels["r"][1][0], 0.7, delta=0.01)
        self.assertAlmostEqual(levels["b"][0][0], 0.3, delta=0.01)
        settings = self.fx.default_settings()
        settings["curves"].update(on=True, **levels)
        out = self.fx._encode(self.fx.Look(settings).apply(img.copy())[..., :3])
        for c in range(3):                                   # cada canal, de negro a blanco
            self.assertLess(np.quantile(out[..., c], 0.002), 0.01)
            self.assertGreater(np.quantile(out[..., c], 0.998), 0.99)
        flat = np.full((10, 10, 4), 0.2, np.float32)          # imagen plana: no se toca
        self.assertEqual(self.fx.auto_levels(flat)["g"], [[0.0, 0.0], [1.0, 1.0]])

    def test_equilibrio_de_color(self):
        fx = self.fx
        enc = np.zeros((1, 3, 3), np.float32)
        enc[0, :, :] = [[0.1] * 3, [0.5] * 3, [0.9] * 3]          # sombra, medio y luz grises
        bal = {"shadows": [1.0, 0.0, 0.0], "midtones": [0.0, 0.0, 0.0], "highlights": [0.0, 0.0, 0.0]}
        out = fx.color_balance(enc.copy(), fx.balance_offsets(bal))[0]
        shift = out[:, 0] - out[:, 1]                               # cuánto más rojo que verde
        self.assertGreater(shift[0], shift[1])
        self.assertGreater(shift[1], shift[2])                      # sobre todo en las sombras
        self.assertGreater(shift[2], 0)
        np.testing.assert_allclose(out @ fx.LUMA, enc[0] @ fx.LUMA, atol=1e-6)   # la luminosidad, igual
        zero = {z: [0.0, 0.0, 0.0] for z in fx.BALANCE_ZONES}
        np.testing.assert_allclose(fx.color_balance(enc.copy(), fx.balance_offsets(zero)), enc)
        s = fx.merged({"balance": {"shadows": [5, "x", 0], "midtones": [0.5, -2, 0]}})["balance"]
        self.assertEqual(s["shadows"], [0.0, 0.0, 0.0])             # roto: a cero
        self.assertEqual(s["midtones"], [0.5, -1.0, 0.0])           # recortado a ±100 %

    def _write_cube(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".cube", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_lut_cube(self):
        fx = self.fx
        g = np.linspace(0, 1, 5)
        lines = [f"{r:.6f} {gg:.6f} {(1 - b):.6f}" for b in g for gg in g for r in g]    # invierte el azul
        path = self._write_cube('TITLE "prueba"\n# comentario\nLUT_3D_SIZE 5\n' + "\n".join(lines) + "\n")
        lut = fx.load_cube(path)
        self.assertEqual(lut[0].shape, (5, 5, 5, 3))
        rng = np.random.default_rng(2)
        enc = rng.random((20, 30, 3)).astype(np.float32)
        out = fx.apply_lut(enc.copy(), lut)
        np.testing.assert_allclose(out[..., :2], enc[..., :2], atol=1e-5)
        np.testing.assert_allclose(out[..., 2], 1 - enc[..., 2], atol=1e-5)
        half = fx.apply_lut(enc.copy(), lut, 0.5)                   # mezcla
        np.testing.assert_allclose(half[..., 2], 0.5, atol=1e-5)
        one_d = fx.load_cube(self._write_cube("LUT_1D_SIZE 2\n0 0 0\n0.5 1 1\n"))
        out = fx.apply_lut(np.full((1, 1, 3), 0.5, np.float32), one_d)
        np.testing.assert_allclose(out[0, 0], [0.25, 0.5, 0.5], atol=1e-6)
        for bad in ("0 0 0\n", "LUT_3D_SIZE 2\n0 0 0\n", "LUT_1D_SIZE 2\n0 0 0\nhola 1 1\n"):
            with self.assertRaises(ValueError):
                fx.load_cube(self._write_cube(bad))
        # en Look: por la ruta, y si el fichero no está, sin efecto (y sin romperse)
        settings = fx.default_settings()
        settings["lut"].update(on=True, path=path)
        img = np.full((4, 4, 4), 1.0, np.float32)
        img[..., :3] = fx._decode(np.float32(0.25))
        out = fx.Look(settings).apply(img.copy())
        self.assertAlmostEqual(float(fx._encode(out[0, 0, 2])), 0.75, places=4)
        settings["lut"]["path"] = path + ".no"
        np.testing.assert_allclose(fx.Look(settings).apply(img.copy()), img)

    def test_enfocar_y_desenfocar(self):
        fx = self.fx
        enc = np.zeros((40, 40, 3), np.float32)
        enc[:, 20:] = 0.6                                           # un borde
        enc[:, :20] = 0.2
        sharp = fx.sharpen(enc.copy(), 1.0, 1.0)
        self.assertGreater(sharp[20, 20, 0], 0.6)                   # el claro sube junto al borde
        self.assertLess(sharp[20, 19, 0], 0.2)                      # y el oscuro baja
        self.assertAlmostEqual(float(sharp[20, 5, 0]), 0.2, places=5)    # lejos, igual
        blurred = fx.sharpen(enc.copy(), -1.0, 2.0)
        np.testing.assert_allclose(blurred, fx._blur_small(enc, 2.0), atol=1e-6)
        self.assertLess(blurred[20, 20, 0], 0.6)
        # el radio grande va por el otro desenfoque y da lo mismo, salvo en los bordes
        np.testing.assert_allclose(fx._blur_small(enc, 2.9)[8:-8, 8:-8], fx._blur_wide(enc, 2.9)[8:-8, 8:-8], atol=2e-3)

    def test_curvas_rotas_en_el_json_vuelven_a_la_recta(self):
        s = self.fx.merged({"curves": {"r": "x", "g": [[0.5, 0.5]], "b": [[1, 2], [0, -1]]}})
        self.assertEqual(s["curves"]["r"], [[0.0, 0.0], [1.0, 1.0]])
        self.assertEqual(s["curves"]["g"], [[0.0, 0.0], [1.0, 1.0]])
        self.assertEqual(s["curves"]["b"], [[0.0, 0.0], [1.0, 1.0]])


class PanelTests(unittest.TestCase):
    def test_pinchar_en_la_barra_salta_alli(self):
        from PySide6.QtTest import QTest
        row = seqview.ValueRow("x", 0, 100, 1, 0, 0.0)
        row.resize(300, 60)
        row.show()
        QCoreApplication.processEvents()
        slider = row.slider
        QTest.mouseClick(slider, Qt.LeftButton, pos=slider.rect().center())
        self.assertGreater(slider.value(), 30)         # a la mitad de la barra, no en 0
        self.assertLess(slider.value(), 70)
        QTest.mouseClick(slider, Qt.LeftButton, Qt.ControlModifier, slider.rect().center())
        self.assertEqual(row.value(), 0.0)              # Ctrl+clic: al valor por defecto
        # arrastrar 40 px: normal mueve diez veces más que con Mayús
        from PySide6.QtCore import QPoint
        def drag(mods):
            row.set_value(50.0)
            start = slider.rect().center()
            QTest.mousePress(slider, Qt.LeftButton, Qt.NoModifier, start)
            from PySide6.QtGui import QMouseEvent
            from PySide6.QtCore import QEvent, QPointF
            end = QPointF(start + QPoint(40, 0))
            QApplication.sendEvent(slider, QMouseEvent(QEvent.MouseMove, end, slider.mapToGlobal(end),
                                                       Qt.NoButton, Qt.LeftButton, mods))
            QTest.mouseRelease(slider, Qt.LeftButton, Qt.NoModifier, start + QPoint(40, 0))
            return row.value() - 50.0
        normal, fine = drag(Qt.NoModifier), drag(Qt.ShiftModifier)
        self.assertGreater(normal, 5)
        self.assertLess(abs(fine), abs(normal) / 4)
        row.close()

    def test_restablecer_un_efecto_y_plegar(self):
        panel = seqview.LookPanel()
        settings = seqview.fx.preset_settings("Super 8 (500T)")
        panel.set_settings(settings)
        panel.reset_effect("vignette")
        after = panel.settings()
        self.assertEqual(after["vignette"], dict(seqview.fx.DEFAULTS["vignette"], on=True))
        self.assertEqual(after["grain"], settings["grain"])        # los demás, igual
        panel.show()
        panel.set_expanded("grain", True)
        self.assertTrue(panel.bodies["grain"].isVisible())
        panel.set_expanded("grain", False)
        self.assertFalse(panel.bodies["grain"].isVisible())
        self.assertIn("grain", panel.folded())
        panel.boxes["grain"].setChecked(False)
        panel.boxes["grain"].setChecked(True)                      # encenderlo no lo despliega
        self.assertFalse(panel.bodies["grain"].isVisible())
        panel.set_expanded("grain", True)
        self.assertTrue(panel.bodies["grain"].isVisible())
        panel.boxes["grain"].setChecked(False)                     # apagado, sigue desplegado
        self.assertTrue(panel.bodies["grain"].isVisible())
        panel.set_expanded("grain", False)
        panel.set_expanded("grain", True)                          # y se puede desplegar apagado
        self.assertTrue(panel.arrows["grain"].isEnabled())
        self.assertTrue(panel.bodies["grain"].isVisible())
        panel.close()

    def test_al_abrir_todo_plegado(self):
        player = seqview.Player([])
        try:
            self.assertEqual(player.panel.folded(), list(seqview.fx.EFFECTS))
        finally:
            player.close()

    def test_deslizadores_de_exposicion_y_gamma(self):
        player = seqview.Player([])
        try:
            sliders = [w for w in player.findChildren(seqview.JumpSlider) if w.width() == 60 or w.maximumWidth() == 60]
            self.assertEqual(len(sliders), 2)
            exposure, gamma = sliders
            exposure.setValue(15)
            self.assertAlmostEqual(player.exposure_box.value(), 1.5)
            gamma.setValue(0)
            self.assertAlmostEqual(player.gamma_box.value(), 1.0)      # el centro es gamma 1
            gamma.setValue(100)
            self.assertAlmostEqual(player.gamma_box.value(), 5.0)
        finally:
            player.close()


    def test_editor_de_curvas(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import QPoint
        panel = seqview.LookPanel()
        panel.boxes["curves"].setChecked(True)
        panel.set_expanded("curves", True)
        panel.show()
        QCoreApplication.processEvents()
        graph = panel.curve_editor.graph
        self.assertEqual(graph.width(), graph.height())
        r = graph._rect()
        at = lambda x, y: graph._to_screen(x, y).toPoint()
        QTest.mouseClick(graph, Qt.LeftButton, pos=at(0.5, 0.75))          # punto nuevo
        pts = panel.settings()["curves"]["master"]
        self.assertEqual(len(pts), 3)
        self.assertAlmostEqual(pts[1][1], 0.75, delta=2 / r.height())
        QTest.mouseClick(graph, Qt.RightButton, pos=at(*pts[1]))         # y se quita
        self.assertEqual(panel.settings()["curves"]["master"], [[0.0, 0.0], [1.0, 1.0]])
        QTest.mouseClick(graph, Qt.RightButton, pos=at(0, 0))            # los extremos no
        self.assertEqual(len(panel.settings()["curves"]["master"]), 2)
        panel.curve_editor.group.buttons()[1].click()                    # canal rojo
        QTest.mouseClick(graph, Qt.LeftButton, pos=at(0.5, 0.2))
        s = panel.settings()
        self.assertEqual(len(s["curves"]["r"]), 3)
        self.assertEqual(len(s["curves"]["master"]), 2)
        other = seqview.LookPanel()
        other.set_settings(s)
        self.assertEqual(other.settings()["curves"], s["curves"])         # ida y vuelta
        panel.reset_effect("curves")
        self.assertEqual(panel.settings()["curves"], dict(seqview.fx.DEFAULTS["curves"], on=True))
        panel.close()
        other.close()

    def test_equilibrio_y_lut_en_el_panel(self):
        panel = seqview.LookPanel()
        ed = panel.balance_editor
        ed.set_zone("shadows")
        ed.rows[0].set_value(0.4, emit=True)
        ed.set_zone("highlights")
        self.assertEqual(ed.rows[0].value(), 0.0)                  # cada zona, sus valores
        ed.rows[2].set_value(-0.3, emit=True)
        s = panel.settings()
        self.assertEqual(s["balance"]["shadows"], [0.4, 0.0, 0.0])
        self.assertEqual(s["balance"]["highlights"], [0.0, 0.0, -0.3])
        self.assertTrue(ed.zone_buttons["shadows"].font().bold())
        self.assertFalse(ed.zone_buttons["midtones"].font().bold())
        s["lut"]["path"] = "/no/existe.cube"
        other = seqview.LookPanel()
        other.set_settings(s)
        self.assertEqual(other.settings()["balance"], s["balance"])  # ida y vuelta
        self.assertEqual(other.settings()["lut"]["path"], "/no/existe.cube")
        self.assertIn("no se puede usar", other.lut_picker.name.text())
        panel.reset_effect("balance")
        self.assertEqual(panel.settings()["balance"], dict(seqview.fx.DEFAULTS["balance"], on=False))
        panel.close()
        other.close()

    def test_boton_auto_de_las_curvas(self):
        from PySide6.QtGui import QImage
        player = seqview.Player([])
        try:
            image = QImage(64, 32, QImage.Format_RGB32)
            image.fill(QColor(20, 40, 90))
            for x in range(32, 64):                         # la mitad derecha, más clara
                for y in range(32):
                    image.setPixelColor(x, y, QColor(200, 180, 230))
            player.cache.images[0] = image
            player.current = 0
            player.panel.boxes["curves"].setChecked(True)
            buttons = [b for b in player.panel.curve_editor.findChildren(seqview.QToolButton) if b.text() == "Auto"]
            self.assertEqual(len(buttons), 1)
            buttons[0].click()
            curves = player.panel.settings()["curves"]
            self.assertEqual(curves["master"], [[0.0, 0.0], [1.0, 1.0]])    # la general, igual
            self.assertNotEqual(curves["r"], [[0.0, 0.0], [1.0, 1.0]])
            self.assertEqual(player.viewer.look.settings["curves"]["r"], curves["r"])   # ya se ve
        finally:
            player.close()

class MuteTests(unittest.TestCase):
    def test_m_silencia_y_vuelve_a_poner_los_efectos(self):
        from PySide6.QtTest import QTest
        player = seqview.Player([])
        try:
            settings = seqview.fx.default_settings()
            settings["vignette"]["on"] = True
            player.panel.set_settings(settings, emit=True)
            QTest.keyClick(player, Qt.Key_M)
            self.assertTrue(player.viewer.bypass)
            self.assertTrue(player.panel.mute.isChecked())
            self.assertTrue(player.panel.settings()["vignette"]["on"])   # sigue encendido, solo silenciado
            QTest.keyClick(player, Qt.Key_M)
            self.assertFalse(player.viewer.bypass)
            QTest.keyClick(player, Qt.Key_M)
            player.panel.apply_preset("Cine 35 mm (500T)")                # elegir una cámara la enseña
            self.assertFalse(player.viewer.bypass)
            QTest.keyClick(player, Qt.Key_M)
            player.panel.set_effect("grade", True)                         # y encender un efecto también
            self.assertFalse(player.viewer.bypass)
            player.panel.mute.click()                                    # el ojo del panel hace lo mismo
            self.assertTrue(player.viewer.bypass)
        finally:
            player.prefs.remove("seqview/look")         # que las demás pruebas no hereden el viñeteado
            player.prefs.remove("seqview/look_muted")
            player.close()


class UserPresetTests(unittest.TestCase):
    def setUp(self):
        i18n.set_language("es")

    def tearDown(self):
        Path(seqview.fx.user_presets_path()).unlink(missing_ok=True)

    def test_guardar_cargar_y_borrar(self):
        panel = seqview.LookPanel()
        settings = seqview.fx.preset_settings("Super 8 (500T)")
        settings["vignette"]["amount"] = -0.9
        settings["sharpen"]["on"] = True
        panel.set_settings(settings)
        self.assertEqual(panel.preset.currentIndex(), 0)              # personalizado
        self.assertFalse(panel.delete_preset.isEnabled())
        panel.store_preset("Mi Super 8")
        self.assertEqual(panel.preset.currentData(), "Mi Super 8")     # ya es un preset
        self.assertTrue(panel.delete_preset.isEnabled())
        other = seqview.LookPanel()                                    # otra ventana, otro proyecto
        other.apply_preset("Mi Super 8")
        self.assertEqual(other.settings()["vignette"]["amount"], -0.9)
        self.assertTrue(other.settings()["sharpen"]["on"])
        self.assertEqual(other.preset.currentData(), "Mi Super 8")
        i18n.set_language("en")                                        # los propios no se traducen
        other._fill_presets()
        self.assertEqual(other.preset.currentText(), "Mi Super 8")
        i18n.set_language("es")
        other.remove_preset("Mi Super 8")
        self.assertEqual(seqview.fx.load_user_presets(), {})
        self.assertEqual(other.preset.currentIndex(), 0)
        self.assertEqual(other.preset.findData("Mi Super 8"), -1)
        panel.close()
        other.close()

    def test_fichero_roto_o_con_nombres_de_fabrica(self):
        path = Path(seqview.fx.user_presets_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{no es json")
        self.assertEqual(seqview.fx.load_user_presets(), {})
        path.write_text('{"Super 8 (50D)": {}, "Viejo": {"grain": {"on": true}, "raro": 1}}')
        presets = seqview.fx.load_user_presets()
        self.assertEqual(list(presets), ["Viejo"])                     # no pisa los de fábrica
        self.assertTrue(presets["Viejo"]["grain"]["on"])
        self.assertEqual(set(presets["Viejo"]), set(seqview.fx.DEFAULTS))


class StartupTests(unittest.TestCase):
    def test_al_abrir_los_efectos_vienen_como_se_dejaron(self):
        settings = seqview.fx.preset_settings("Cine 35 mm (500T)")
        first = seqview.Player([])
        try:
            first.panel.set_settings(settings, emit=True)          # se guardan
            first.panel.mute.setChecked(False)
        finally:
            first.close()
        player = seqview.Player([])
        try:
            self.assertFalse(player.viewer.bypass)                  # activos, como se dejaron
            self.assertFalse(player.panel.mute.isChecked())
            self.assertEqual(player.panel.settings(), settings)
            player.panel.mute.setChecked(True)                      # se cierra silenciado...
        finally:
            player.close()
        player = seqview.Player([])
        try:
            self.assertTrue(player.viewer.bypass)                   # ...y se abre silenciado
            self.assertTrue(player.panel.mute.isChecked())
            self.assertEqual(player.panel.settings(), settings)     # sin perder lo que había
        finally:
            player.prefs.remove("seqview/look")
            player.prefs.remove("seqview/look_muted")
            player.close()


def tearDownModule():
    # Que no quede ningún hilo (la búsqueda de la secuencia va en el pool global) ni ventana
    # por borrar cuando Python destruye Qt al salir: si no, a veces acaba en violación de segmento.
    from PySide6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone()
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    QCoreApplication.processEvents()

if __name__ == "__main__":
    unittest.main()
