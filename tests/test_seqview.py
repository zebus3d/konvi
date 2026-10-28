"""El visor de secuencias integrado (seqview), sin pantalla:  python3 -m unittest tests.test_seqview"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_config = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _config.name       # que los ajustes guardados no toquen los de verdad

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
            player.close()


class StartupTests(unittest.TestCase):
    def test_al_abrir_los_efectos_guardados_vienen_silenciados(self):
        settings = seqview.fx.preset_settings("Cine 35 mm (500T)")
        first = seqview.Player([])
        try:
            first.panel.set_settings(settings, emit=True)          # se guardan
        finally:
            first.close()
        player = seqview.Player([])
        try:
            self.assertTrue(player.viewer.bypass)                   # se ve el original
            self.assertTrue(player.panel.mute.isChecked())
            self.assertEqual(player.panel.settings(), settings)     # pero conserva lo que había
        finally:
            player.prefs.remove("seqview/look")
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
