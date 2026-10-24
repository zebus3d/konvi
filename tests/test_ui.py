"""La interfaz, sin pantalla y convirtiendo de verdad clips cortos (unos segundos).

    python3 -m unittest tests.test_ui
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QElapsedTimer, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QComboBox, QWidget  # noqa: E402

from konvi import app as konvi_app  # noqa: E402
from konvi.core import command, presets  # noqa: E402
from konvi.core.jobs import Status  # noqa: E402
from konvi.ui import main_window, workers  # noqa: E402
from konvi.ui.preset_dialog import PresetDialog  # noqa: E402

qapp = QApplication.instance() or QApplication([])
P = presets.builtin_by_id()


def wait_until(cond, ms=60000):
    t = QElapsedTimer()
    t.start()
    while not cond() and t.elapsed() < ms:
        QCoreApplication.processEvents()
    return cond()


def no_gpu(self, cache_file):
    self.hardware_ready.emit(command.NO_HARDWARE)


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.dir = Path(cls.tmp.name)
        lavfi = ["-f", "lavfi", "-i"]
        for name, secs in (("corto", 1), ("largo", 20)):
            subprocess.run(["ffmpeg", "-v", "error", *lavfi, f"testsrc2=size=640x360:rate=25:duration={secs}",
                            *lavfi, f"sine=duration={secs}", "-c:v", "libx264", "-preset", "ultrafast",
                            "-c:a", "aac", "-shortest", str(d / f"{name}.mp4")], check=True)
        subprocess.run(["ffmpeg", "-v", "error", *lavfi, "sine=duration=1", str(d / "musica.mp3")], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        # nada de tocar los ajustes, perfiles ni la tarjeta gráfica del usuario
        self.home = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.home.name) / "konvi.ini"), QSettings.IniFormat)
        patcher = mock.patch.object(workers.Workers, "detect_hardware", no_gpu)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.out = Path(self.home.name) / "salida"
        self.out.mkdir()
        self.settings.setValue("out_folder", str(self.out))
        self.w = main_window.MainWindow(settings=self.settings, config_dir=Path(self.home.name) / "cfg")
        self.w.show()

    def tearDown(self):
        if self.w.runner.running:
            self.w.runner.stop()
            self.w.runner.wait()
        self.w.workers.wait()
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.processEvents()
        self.home.cleanup()

    def add(self, *names):
        self.w.add_files([str(self.dir / n) for n in names])
        self.assertTrue(wait_until(lambda: all(j.status != Status.PROBING for j in self.w.jobs)))

    def choose(self, preset_id):
        self.w._choose(self.w.all_presets()[preset_id])

    def test_vacio(self):
        self.assertEqual(self.w.stack.currentIndex(), 0)
        self.assertFalse(self.w.btn_convert.isEnabled())
        self.assertIn("Añade", self.w.btn_convert.toolTip())

    def test_anadir_y_leer(self):
        self.add("corto.mp4", "musica.mp3")
        self.assertEqual(self.w.stack.currentIndex(), 1)
        video, audio = self.w.jobs
        self.assertEqual(video.status, Status.READY)
        self.assertEqual(audio.status, Status.INVALID)          # un mp3 con un perfil de vídeo
        self.assertIn("Solo audio", audio.message)
        self.assertTrue(self.w.btn_convert.isEnabled())
        self.add("corto.mp4")                                    # repetido: no se duplica
        self.assertEqual(len(self.w.jobs), 2)

    def test_perfil_a_todos_o_a_los_seleccionados(self):
        self.add("corto.mp4", "largo.mp4")
        self.choose("audio_mp3")
        self.assertEqual({j.preset.id for j in self.w.jobs}, {"audio_mp3"})
        self.w.items[self.w.jobs[0].id].setSelected(True)
        self.choose("web_whatsapp")
        self.assertEqual([j.preset.id for j in self.w.jobs], ["web_whatsapp", "audio_mp3"])
        self.assertEqual(self.w.default_preset.id, "audio_mp3")  # elegir para unos no cambia el de fábrica
        self.assertIn("seleccionado", self.w.target_label.text())

    def test_convertir(self):
        self.add("corto.mp4", "musica.mp3")
        self.w.jobs[1].set_preset(P["audio_m4a"])
        self.w.convert()
        self.assertTrue(self.w.btn_stop.isVisible())
        self.assertTrue(wait_until(lambda: not self.w.runner.running))
        for job in self.w.jobs:
            self.assertEqual(job.status, Status.DONE, job.message)
            self.assertTrue(job.output.exists())
            self.assertEqual(job.output.parent, self.out)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["corto.mp4", "musica.m4a"])
        self.assertIn("convertidos", self.w.overall_label.text())
        self.assertTrue(self.w.btn_convert.isVisible())

    def test_nunca_sobrescribe(self):
        (self.out / "corto.mp4").write_bytes(b"de antes")
        self.add("corto.mp4")
        self.w.convert()
        self.assertTrue(wait_until(lambda: not self.w.runner.running))
        self.assertEqual((self.out / "corto.mp4").read_bytes(), b"de antes")
        self.assertEqual(self.w.jobs[0].output.name, "corto (2).mp4")

    def test_pausa_y_detener(self):
        self.add("largo.mp4")
        self.choose("mkv_av1")                                  # lento: da tiempo a pausar
        self.w.convert()
        job = self.w.jobs[0]
        self.assertTrue(wait_until(lambda: job.fraction > 0))
        self.w._toggle_pause()
        self.assertEqual(job.status, Status.PAUSED)
        self.assertEqual(self.w.btn_pause.text(), "Continuar")
        self.w._toggle_pause()
        self.assertEqual(job.status, Status.RUNNING)
        self.w._stop()
        self.assertTrue(wait_until(lambda: not self.w.runner.running))
        self.assertEqual(job.status, Status.CANCELLED)
        self.assertEqual(list(self.out.iterdir()), [])          # ni el temporal ni nada a medias

    def test_volver_a_convertir(self):
        self.add("corto.mp4")
        self.w.convert()
        self.assertTrue(wait_until(lambda: not self.w.runner.running))
        self.w._retry(self.w.jobs[0])
        self.assertEqual(self.w.jobs[0].status, Status.READY)

    def test_perfil_propio(self):
        dlg = PresetDialog(P["mp4_h264"], self.w)
        dlg.slider.setValue(0)                                   # «Baja»
        mine = dlg.result_preset("custom_1")
        self.assertEqual((mine.quality, mine.custom, mine.category), (26, True, "custom"))
        dlg.by_size.setChecked(True)
        dlg.size_mb.setValue(50)
        self.assertEqual(dlg.result_preset("custom_1").target_mb, 50)
        lossless = PresetDialog(P["mp4_lossless"], self.w)
        self.assertFalse(lossless.slider.isEnabled())
        self.assertIn("sin pérdida", lossless.slider.toolTip())

    def test_mis_perfiles(self):
        self.assertTrue(self.w.category_buttons["custom"].isHidden())
        mine = presets.Preset("custom_1", "custom", "Mío", "", "mp4", custom=True)
        self.w.custom = [mine]
        self.w._save_custom()
        self.w._choose(mine)
        self.assertFalse(self.w.category_buttons["custom"].isHidden())
        again = main_window.MainWindow(settings=self.settings, config_dir=self.w.config_dir)
        self.assertEqual(again.default_preset.id, "custom_1")    # se recuerda entre sesiones
        again.workers.wait()
        again.deleteLater()

    def test_tooltips(self):
        for w in self.w.findChildren(QWidget):
            if isinstance(w, (QAbstractButton, QComboBox)) and w.isVisibleTo(self.w) \
                    and w.parent() is not None and w.objectName() != "qt_scrollarea_hcontainer":
                if isinstance(w, QAbstractButton) and w.parent().inherits("QDialogButtonBox"):
                    continue
                self.assertTrue(w.toolTip(), f"{type(w).__name__} «{getattr(w, 'text', lambda: '')()}» sin tooltip")

    def test_una_sola_ventana(self):
        server = konvi_app.listen(self.w)
        self.addCleanup(server.close)
        self.assertTrue(konvi_app.send_to_running([str(self.dir / "corto.mp4")]))
        self.assertTrue(wait_until(lambda: len(self.w.jobs) == 1, 5000))


if __name__ == "__main__":
    unittest.main()
