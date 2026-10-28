"""El visor de secuencias integrado (seqview), sin pantalla:  python3 -m unittest tests.test_seqview"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QElapsedTimer, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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
