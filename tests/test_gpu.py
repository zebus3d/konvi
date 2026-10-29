"""Los efectos en la gráfica contra los de numpy:  python3 -m unittest tests.test_gpu

Se prueban en cada gráfica que haya; sin ninguna (un servidor sin OpenGL 4.3), se saltan."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_config = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _config.name
os.environ["XDG_CACHE_HOME"] = _config.name        # la medida de las gráficas, aparte de la de verdad

import numpy as np  # noqa: E402

from konvi.core import gpu  # noqa: E402
from konvi.core import look as fx  # noqa: E402

DEVICES = gpu.Engine().devices()


def test_image(w=320, h=180):
    """Degradados (para que se vea si algo se desplaza), ruido suave y unas luces fuertes
    (para el glow y la halación)."""
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    rng = np.random.default_rng(3)
    img = np.stack([(x / w) ** 2.2, (y / h) ** 2.2, 0.2 + 0.1 * rng.random((h, w)),
                    0.5 + 0.5 * (x / w)], -1).astype(np.float32)
    img[h // 3:h // 3 + 6, w // 4:w // 4 + 6, :3] = 20.0
    img[2 * h // 3, :, :3] = 4.0
    return img


CASES = {
    "distorsión": {"distortion": {"on": True, "amount": -28.0}},
    "distorsión sin encajar": {"distortion": {"on": True, "amount": 5.0, "fill": False}},
    "aberración": {"aberration": {"on": True, "amount": 0.3}},
    "glow": {"glow": {"on": True, "saturation": 0.5}},
    "solo glow": {"glow": {"on": True, "only": True, "size": 1.0, "falloff": 1.5}},
    "viñeteado": {"vignette": {"on": True, "amount": -2, "midpoint": 0.1, "roundness": 0.3, "fit": 0.5}},
    "halación": {"halation": {"on": True, "amount": 0.5}},
    "grano de película": {"grain": {"on": True, "kind": "film", "stock": "500T", "gauge": "8"}},
    "ruido digital": {"grain": {"on": True, "kind": "digital", "iso": 6400, "nr": 0.5}},
    "color": {"grade": {"on": True, "temperature": 3200, "tint": 10, "exposure": 0.5, "contrast": 1.3,
                        "gamma": 1.2, "saturation": 1.4}},
    "luces": {"highlights": {"on": True}},
    "equilibrio": {"balance": {"on": True, "shadows": [0.5, 0, -0.5], "highlights": [0, 0.3, 0]}},
    "curvas": {"curves": {"on": True, "master": [[0, 0.05], [0.5, 0.6], [1, 1]],
                          "r": [[0, 0], [0.3, 0.2], [1, 1]]}},
    "enfoque": {"sharpen": {"on": True, "amount": 1.0, "radius": 1.5}},
    "desenfoque ancho": {"sharpen": {"on": True, "amount": -1.0, "radius": 8.0}},
    "enteros en el JSON": {"vignette": {"on": True, "amount": -2}, "grade": {"on": True, "saturation": 2}},
}


def close(test, got, want, what, mean=1e-4):
    """Lo mismo salvo redondeos: en relativo (con un suelo, para los negros)."""
    d = np.abs(got.astype(np.float32) - want) / (np.abs(want) + 0.05)
    test.assertLess(float(d.max()), 1e-2, what)
    test.assertLess(float(d.mean()), mean, what)


@unittest.skipUnless(DEVICES, "no hay ninguna gráfica con OpenGL 4.3")
class GpuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lut = Path(_config.name) / "prueba.cube"
        g = np.linspace(0, 1, 9)
        with open(cls.lut, "w") as f:
            f.write("LUT_3D_SIZE 9\n")
            for b in g:
                for gg in g:
                    for r in g:
                        f.write(f"{r ** 1.2:.6f} {gg ** 0.8:.6f} {b * 0.9 + 0.05:.6f}\n")
        cls.engines = [gpu.Engine(d) for d in DEVICES]

    @classmethod
    def tearDownClass(cls):
        for e in cls.engines:
            e.close()

    def cases(self):
        cases = dict(CASES, LUT={"lut": {"on": True, "path": str(self.lut), "mix": 0.8}})
        everything = {}
        for c in cases.values():
            for k, v in c.items():
                everything.setdefault(k, v)
        cases["todos a la vez"] = everything
        for name in fx.PRESETS:
            cases[name] = fx.preset_settings(name)
        return cases

    def test_cada_efecto_igual_que_numpy(self):
        img = test_image()
        for name, s in self.cases().items():
            want = fx.Look(s).apply(img.copy(), 7)
            for e in self.engines:
                with self.subTest(efecto=name, grafica=e.choice["name"]):
                    got = e.apply(fx.merged(s), img, 7)
                    self.assertIsNotNone(got, e.error)
                    close(self, got, want, name)

    def test_revelar_desde_el_fichero(self):
        """El visor le pasa los píxeles tal cual: 8 o 16 bits en sRGB, o half lineal."""
        s = fx.preset_settings("Cine 35 mm (500T)")
        rng = np.random.default_rng(5)
        u8 = (rng.random((90, 160, 4)) * 255).astype(np.uint8)
        u16 = (rng.random((90, 160, 4)) * 65535).astype(np.uint16)
        f16 = (rng.random((90, 160, 4)) * 3).astype(np.float16)

        def linear(src):
            if src.dtype.kind == "f":
                return src.astype(np.float32)
            v = src.astype(np.float32) / np.float32(np.iinfo(src.dtype).max)
            out = fx._decode(v).astype(np.float32)
            out[..., 3] = v[..., 3]
            return out
        for src in (u8, u16, f16):
            want = fx.Look(s).apply(linear(src), 3)
            for e in self.engines:
                with self.subTest(tipo=src.dtype.name, grafica=e.choice["name"]):
                    got = e.develop(s, src, 3, lambda half: half.copy())
                    self.assertEqual(got.dtype, np.float16)
                    # en half: un escalón es 1/2048, y hay gráficas que redondean hacia cero
                    close(self, got, want, src.dtype.name, mean=1e-3)

    def test_la_automatica_es_la_mas_rapida(self):
        """Se miden una vez y se guarda; mientras no cambien las tarjetas, no se vuelve a medir."""
        ms = [d["ms"] for d in DEVICES]
        self.assertEqual(ms, sorted(ms))
        path = gpu._ranking_path()
        self.assertTrue(path.exists())
        stamp = path.stat().st_mtime_ns
        self.assertEqual([d["name"] for d in gpu.list_devices()], [d["name"] for d in DEVICES])
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        e = gpu.Engine("auto")
        self.assertIsNotNone(e.apply(fx.default_settings(), test_image(32, 18)), e.error)
        self.assertEqual(e.name, DEVICES[0]["name"])
        e.close()

    def test_vuelve_a_abrirse_si_se_cerro(self):
        """Sin trabajo, el proceso se cierra solo (para que la gráfica duerma)."""
        e = self.engines[0]
        s = fx.preset_settings("Objetivo antiguo")
        img = test_image(64, 36)
        self.assertIsNotNone(e.apply(s, img))
        e.proc.stdin.close()            # como si hubiera pasado el rato sin trabajo
        e.proc.wait(5)
        self.assertIsNotNone(e.apply(s, img), e.error)
        self.assertIsNone(e.error)


@unittest.skipUnless(DEVICES, "no hay ninguna gráfica con OpenGL 4.3")
class ViewerTests(unittest.TestCase):
    """Lo que pinta el visor, con la gráfica y sin ella."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from konvi.ui import seqview
        cls.sv = seqview

    @classmethod
    def tearDownClass(cls):
        cls.sv.GPU.close()

    def pixels(self, image):
        from PySide6.QtGui import QImage
        q = image.convertToFormat(QImage.Format_RGBA8888)
        a = np.frombuffer(q.constBits(), np.uint8).reshape(q.height(), q.bytesPerLine() // 4, 4)
        return a[:, :q.width()].astype(int)

    def test_lo_que_se_ve_es_lo_mismo(self):
        from PySide6.QtGui import QImage
        sv = self.sv
        px = (np.clip(test_image(), 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
        h, w = px.shape[:2]
        img8 = QImage(px.data, w, h, w * 4, QImage.Format_RGBA8888).copy()
        look = fx.Look(fx.preset_settings("Cine 35 mm (500T)"))
        for image in (img8, img8.convertToFormat(QImage.Format_RGBA64),
                      img8.convertToFormat(QImage.Format_RGBA16FPx4)):
            with self.subTest(formato=image.format()):
                sv.GPU.set_choice("cpu")
                want = self.pixels(sv.develop(image, 0.5, 1.0, "rgb", look, 12))
                sv.GPU.set_choice("auto")
                got = self.pixels(sv.develop(image, 0.5, 1.0, "rgb", look, 12))
                self.assertIsNotNone(sv.GPU.name, sv.GPU.error)
                # de redondeo: un nivel de 255, y alguno suelto dos (en un borde muy marcado)
                d = np.abs(got - want)
                self.assertLessEqual(int(d.max()), 2)
                self.assertLess(float((d > 1).mean()), 1e-4)

    def test_preferencia(self):
        from PySide6.QtCore import QSettings
        prefs = QSettings("konvi-prueba", "konvi-prueba")
        old = os.environ.pop("KONVI_GPU", None)
        try:
            prefs.clear()
            self.assertEqual(self.sv.gpu_choice(prefs), "auto")
            prefs.setValue("seqview/gpu", '{"name": "X", "node": "/dev/dri/renderD128"}')
            self.assertEqual(self.sv.gpu_choice(prefs)["name"], "X")
            prefs.setValue("seqview/gpu", "no es json")
            self.assertEqual(self.sv.gpu_choice(prefs), "auto")
            os.environ["KONVI_GPU"] = '"cpu"'
            self.assertEqual(self.sv.gpu_choice(prefs), "cpu")
        finally:
            os.environ.pop("KONVI_GPU", None)
            if old is not None:
                os.environ["KONVI_GPU"] = old
        self.assertEqual(self.sv.gpu_label("NVIDIA GeForce RTX 3070 Laptop GPU/PCIe/SSE2"),
                         "NVIDIA GeForce RTX 3070 Laptop GPU")
        self.assertEqual(self.sv.gpu_label("AMD Radeon Graphics (radeonsi, renoir, ACO, DRM 3.64)"),
                         "AMD Radeon Graphics")


class FallbackTests(unittest.TestCase):
    @unittest.skipUnless(DEVICES, "no hay ninguna gráfica con OpenGL 4.3")
    def test_si_ya_no_esta_la_elegida_usa_la_mas_rapida(self):
        e = gpu.Engine({"name": "una tarjeta que se quitó", "node": "/dev/dri/renderD999"})
        self.assertIsNotNone(e.apply(fx.preset_settings("Objetivo antiguo"), test_image(64, 36)), e.error)
        self.assertEqual(e.name, DEVICES[0]["name"])
        e.close()

    def test_si_la_grafica_falla_avisa_y_sigue_numpy(self):
        broken = Path(_config.name) / "rota.py"
        broken.write_text('import json\nprint(json.dumps({"error": "el driver se ha caído"}), flush=True)\n')
        errors = []
        e = gpu.Engine("auto")
        e.worker = [sys.executable, str(broken)]
        e.on_error = errors.append
        img = test_image(64, 36)
        self.assertIsNone(e.apply(fx.preset_settings("Objetivo antiguo"), img))
        self.assertIsNone(e.apply(fx.preset_settings("Objetivo antiguo"), img))    # no reintenta
        self.assertEqual(errors, ["el driver se ha caído"])
        e.set_choice("cpu")
        self.assertIsNone(e.error)          # cambiar de gráfica lo vuelve a intentar
        e.close()

    def test_en_cpu_ni_lo_intenta(self):
        e = gpu.Engine("cpu")
        self.assertIsNone(e.apply(fx.default_settings(), test_image(16, 9)))
        self.assertIsNone(e.proc)


if __name__ == "__main__":
    unittest.main()
