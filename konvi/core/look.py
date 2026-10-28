"""Efectos de cámara de seqview: distorsión, aberración cromática, viñeteado, halación, grano y
un etalonaje sencillo (balance de blancos, brillo, contraste, gamma, saturación).

Todo trabaja en luz lineal sobre un array float32 (alto, ancho, 4), RGBA sin premultiplicar,
en el orden en que le pasa a la luz: primero el objetivo (distorsión, aberración cromática y
viñeteado), después la película o el sensor (halación, grano o ruido) y por último el
etalonaje, que se hace sobre lo ya rodado. Sin Qt: lo usa el visor para
enseñarlo y la exportación para escribirlo, así que lo que se ve es lo que se guarda.

De dónde salen los valores por defecto:

- Viñeteado: LensTip mide en la esquina de una full frame, a f/1.4, -0,75 EV (Pentax FA 50),
  -1,00 (Nikon AF-S 50 G), -1,63 (Sigma Art 50) y -3,12 (Zeiss Milvus 50). La mediana es
  -1,3 EV. La forma de la caída, 2^(EV·r²), es la de los perfiles medidos de lensfun para los
  Canon EF 50/1.4 y Nikon 50/1.4G abiertos: más plana en el centro y más rápida al llegar a la
  esquina que la ley cos⁴ sola.
- Distorsión: TV distortion (SMIA), la medida de Imatest. Un 50 mm f/1.4 da -1,0 a -1,3 %
  (calculado de los coeficientes de lensfun; LensTip publica la distorsión radial en la
  esquina, unas 1,4-1,5 veces mayor). Una GoPro sin corregir, en «Wide», unos -30 %. El modelo
  es el de división (Fitzgibbon), que aguanta ese barril sin plegarse.
- Aberración cromática lateral: en % de la distancia del centro a la esquina, como Imatest y
  LensTip; 0,04 % es baja, 0,08 % el borde entre baja y media y 0,12 % media. El modelo
  (repartir cada canal a lo largo del espectro en vez de desplazar R, G y B) es el del nodo
  Lens Distortion del compositor de Blender.
- Grano de película: las curvas «Diffuse rms Granularity» de las fichas técnicas de Kodak
  VISION3 (σD medida con una apertura circular de 48 µm), pasadas a cada píxel con la ley de
  Selwyn según el tamaño del fotograma (35 mm, 16 mm, Super 8) y la resolución de la imagen.
  La ley deja de cumplirse cerca del tamaño de una nube de colorante (10-15 µm), así que el
  área se suaviza con la de la nube, y el escáner (óptica, sobremuestreo) quita algo más.
  En exposición el grano es constante en la parte recta de la curva y crece en el pie, donde
  la curva se aplana: por eso un negativo subexpuesto se ve con más grano. Cada capa tiene su
  propio grano, algo correlado entre capas (colorantes que se solapan, máscaras).
- Halación: la luz que atraviesa la emulsión rebota en el soporte y vuelve sobre todo a la
  capa roja (la más cercana al soporte): halo naranja-rojo alrededor de las luces fuertes,
  de unos 0,1-0,2 mm en un VISION3 con su capa antihalo y mucho mayor sin ella (CineStill).
- Ruido digital: ruido de fotones (crece con la raíz de la señal) más el de lectura (Brooks
  et al. 2019, «Unprocessing images»), con la relación señal/ruido en gris medio de una full
  frame actual: DxOMark da su «Low-light ISO» (30 dB) en 3000-3700 para A7 III, Z6, R5 o R6,
  lo que son unos 45 dB a ISO 100 y 3 dB menos por paso, normalizado a 8 Mpx. El azul y el
  rojo llevan más ruido que el verde (la ganancia del balance de blancos). Un móvil o una
  cámara de acción, con un sensor 4-5 pasos más pequeño y reducción de ruido: menos ruido de
  luminancia y manchas de color más grandes.
"""

import copy
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

# ------------------------------------------------------------------ ajustes

DEFAULTS = {
    "distortion": {"on": False, "amount": -1.0, "fill": True},
    "aberration": {"on": False, "amount": 0.08},
    "vignette": {"on": False, "amount": -1.3, "midpoint": 0.0, "roundness": 0.0, "fit": 0.0},
    "halation": {"on": False, "amount": 0.02, "radius": 0.15, "threshold": 2.0},
    "grain": {"on": False, "kind": "film", "stock": "250D", "gauge": "35", "iso": 3200,
              "amount": 1.0, "color": 1.0, "nr": 0.0},
    "grade": {"on": False, "temperature": 6500.0, "tint": 0.0, "exposure": 0.0, "contrast": 1.0,
              "gamma": 1.0, "saturation": 1.0},
}
EFFECTS = ("distortion", "aberration", "vignette", "halation", "grain", "grade")

# σD × 1000 a media escala (densidad ~1), leídas de las curvas de las fichas de Kodak, en el
# orden R, G, B. La capa azul (la que forma el amarillo) es siempre la de más grano.
STOCKS = {
    "50D": ("VISION3 50D (5203)", (5, 5, 8)),
    "250D": ("VISION3 250D (5207)", (6, 7, 11)),
    "500T": ("VISION3 500T (5219)", (6, 8, 17)),
}
# ancho de la ventanilla de cámara en mm
GAUGES = {
    "35": ("35 mm (Super 35)", 24.89),
    "16": ("Super 16", 12.52),
    "8": ("Super 8", 5.79),
}
ISOS = (100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600, 51200)

NEGATIVE_GAMMA = 0.6        # pendiente de los VISION3 (la que suponen Cineon y Kodak)
APERTURE_UM = 48.0          # la del microdensitómetro de Kodak (circular)
DYE_CLOUD_UM = 10.0         # nube de colorante (10-15 µm): por debajo, píxeles vecinos ven el mismo grano
SCAN_FACTOR = 0.6           # lo que quitan la óptica del escáner y el sobremuestreo (estimado: 0,5-0,7)
TOE_STOPS = 5.0             # el pie de la curva empieza unos 5 pasos por debajo del gris medio
TOE_MAX_BOOST = 2.5         # en el pie el grano crece, pero no sin límite (2-3 veces)
LAYER_CORRELATION = 0.2     # cuánto comparten grano las capas (0,15-0,3)
HALATION_TINT = (1.0, 0.25, 0.02)   # vuelve sobre todo a la capa roja; algo a la verde: naranja
SNR18_ISO100_DB = 45.0      # full frame actual, gris medio, normalizado a 8 Mpx (DxOMark)
READ_RATIO = ((100, 0.03), (800, 0.06), (3200, 0.10), (12800, 0.15))   # σ lectura / σ en gris
SENSOR_CHANNEL_GAIN = (1.25, 1.0, 1.5)     # R G B: el balance de blancos sube rojo y azul

# Cámaras completas, de la más usada en cine a la menos: el 500T en 35 mm es la emulsión con
# la que más se rueda en película (también de día, filtrada), luego el 250D; el 16 mm y el
# Super 8 se usan para un aspecto concreto (documental, recuerdos). Lo que no se nombra se
# queda apagado.
PRESETS = {
    "Cine digital (Super 35)": {
        # cámara de cine digital a su ISO base: ruido fino, casi todo de luminancia
        "distortion": {"on": True, "amount": -0.3},
        "aberration": {"on": True, "amount": 0.04},
        "vignette": {"on": True, "amount": -0.5},
        "grain": {"on": True, "kind": "digital", "iso": 800, "color": 0.3}},
    "Cine 35 mm (500T)": {
        # primes de cine en Super 35: poca distorsión; el viñeteado, el de un 50 mm abierto
        # en APS-C (LensTip: -0,24 a -0,69 EV)
        "distortion": {"on": True, "amount": -0.3},
        "aberration": {"on": True, "amount": 0.05},
        "vignette": {"on": True, "amount": -0.7},
        "halation": {"on": True},
        "grain": {"on": True, "kind": "film", "stock": "500T", "gauge": "35"}},
    "Cine 35 mm (250D)": {
        "distortion": {"on": True, "amount": -0.3},
        "aberration": {"on": True, "amount": 0.04},
        "vignette": {"on": True, "amount": -0.5},
        "halation": {"on": True},
        "grain": {"on": True, "kind": "film", "stock": "250D", "gauge": "35"}},
    "16 mm (500T)": {
        "distortion": {"on": True, "amount": -1.5},
        "aberration": {"on": True, "amount": 0.10},
        "vignette": {"on": True, "amount": -1.5},
        "halation": {"on": True},
        "grain": {"on": True, "kind": "film", "stock": "500T", "gauge": "16"}},
    "Super 8 (50D)": {
        "distortion": {"on": True, "amount": -3.0},
        "aberration": {"on": True, "amount": 0.2},
        "vignette": {"on": True, "amount": -2.0, "midpoint": 0.1},
        "halation": {"on": True},
        "grain": {"on": True, "kind": "film", "stock": "50D", "gauge": "8"}},
    "Super 8 (500T)": {
        "distortion": {"on": True, "amount": -3.0},
        "aberration": {"on": True, "amount": 0.2},
        "vignette": {"on": True, "amount": -2.0, "midpoint": 0.1},
        "halation": {"on": True},
        "grain": {"on": True, "kind": "film", "stock": "500T", "gauge": "8"}},
    "Objetivo antiguo": {
        # un Helios 44 a f/2: -1,7 EV en la esquina (OpticalLimits); aberración «muy grande»
        # en la escala de LensTip
        "distortion": {"on": True, "amount": -1.5},
        "aberration": {"on": True, "amount": 0.15},
        "vignette": {"on": True, "amount": -1.8}},
    "Réflex full frame, 50 mm f/1.4": {
        "distortion": {"on": True, "amount": -1.2},
        "aberration": {"on": True, "amount": 0.08},
        "vignette": {"on": True, "amount": -1.3},
        "grain": {"on": True, "kind": "digital", "iso": 800, "color": 0.7}},
    "Cámara de acción": {
        # gran angular sin corregir (lensfun, GoPro HERO10: unos -31 % SMIA); sensor de 1/2,3",
        # 4,9 pasos más pequeño que una full frame: a ISO 100 es como una full frame a 3200
        "distortion": {"on": True, "amount": -28.0},
        "aberration": {"on": True, "amount": 0.15},
        "vignette": {"on": True, "amount": -0.7},
        "grain": {"on": True, "kind": "digital", "iso": 3200, "color": 0.7, "nr": 0.5}},
    "Móvil de noche": {
        # distorsión ya corregida por el teléfono; varias tomas fundidas y mucha reducción de ruido
        "aberration": {"on": True, "amount": 0.05},
        "vignette": {"on": True, "amount": -0.5},
        "grain": {"on": True, "kind": "digital", "iso": 6400, "color": 0.7, "nr": 1.0}},
}


def default_settings():
    return copy.deepcopy(DEFAULTS)


def merged(settings):
    """Los ajustes completos: lo que falte (de un JSON viejo, por ejemplo) sale de DEFAULTS."""
    out = default_settings()
    for effect, values in (settings or {}).items():
        if effect in out and isinstance(values, dict):
            out[effect].update({k: v for k, v in values.items() if k in out[effect]})
    return out


def preset_settings(name):
    """Un preset sobre los valores por defecto, con todo lo que no nombra apagado."""
    out = default_settings()
    for effect, values in PRESETS[name].items():
        out[effect].update(values)
    return out


def matching_preset(settings):
    for name in PRESETS:
        if merged(settings) == preset_settings(name):
            return name
    return None


# ------------------------------------------------------------------ hilos

_pool = ThreadPoolExecutor(max(2, min(8, (os.cpu_count() or 4) // 2)))


def by_bands(height, work, rows=16):
    """Reparte el trabajo por franjas de filas entre varios hilos (numpy suelta el GIL).
    Franjas estrechas: los arrays intermedios caben en la caché y se reutiliza la memoria en
    vez de pedirle al sistema páginas nuevas, que es lo que de verdad cuesta (con franjas de
    un octavo de imagen, la aberración tardaba 240 ms; así, una fracción)."""
    list(_pool.map(lambda y: work(slice(y, min(y + rows, height))), range(0, height, rows)))


# ------------------------------------------------------------------ distorsión y aberración

def _tv_of(lam, w, h):
    """TV distortion (SMIA) que da el modelo de división con ese λ: se fotografía un
    rectángulo cuyas esquinas caen en las del fotograma y se mide cuánto se comba su borde de
    arriba, (A - B) / B, con A la altura en los lados y B en el centro."""
    half = math.hypot(w, h) / 2
    a = h / 2 / half
    y0 = a / (1 + lam)                      # altura sin distorsionar del borde del rectángulo
    if abs(lam) < 1e-12:
        return 0.0
    b = (1 - math.sqrt(max(0.0, 1 - 4 * lam * y0 * y0))) / (2 * lam * y0)
    return (a - b) / b * 100


def distortion_lambda(tv_percent, w, h):
    """λ del modelo de división r_u = r_d / (1 + λ r_d²) (r = 1 en la esquina) que da esa TV
    distortion. Negativo = barril, positivo = cojín. A diferencia del polinomio de Brown, no se
    pliega con un barril fuerte (un gran angular de cámara de acción)."""
    if not tv_percent:
        return 0.0
    lo, hi = -0.95, 0.95
    for _ in range(60):
        mid = (lo + hi) / 2
        if _tv_of(mid, w, h) < tv_percent:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _lerp_table(n, mag):
    """Escalar un eje de n píxeles por mag respecto al centro: índice de la muestra de la
    izquierda, peso de la de la derecha y si cae dentro de la imagen."""
    x = (np.arange(n, dtype=np.float64) + 0.5 - n / 2) / mag + n / 2 - 0.5
    inside = ((x >= -0.5) & (x <= n - 0.5)).astype(np.float32)
    x = np.clip(x, 0, n - 1)
    i0 = np.minimum(np.floor(x).astype(np.intp), max(n - 2, 0))
    return i0, (x - i0).astype(np.float32), inside


class _Warp:
    """Distorsión (un remuestreo bilineal por píxel, con el mapa calculado una vez) y después
    aberración cromática lateral: cada longitud de onda es la misma imagen con un aumento un
    poco distinto, así que es un escalado respecto al centro, que se hace por filas y columnas
    (mucho más barato que un remuestreo libre)."""

    def __init__(self, w, h, tv, ca, fill):
        self.w, self.h = w, h
        half = math.hypot(w, h) / 2
        # aberración: el rojo se ve ca/2 % más grande y el azul ca/2 % más pequeño; tantos
        # pasos como píxeles separan los extremos, para que no se vean tres copias sueltas
        ca_px = abs(ca) / 100 * half
        steps = 0 if not ca else int(min(9, max(3, 2 * math.ceil(ca_px / 2) + 1)))
        t = np.linspace(0, 1, steps)
        mags = 1 + (t - 0.5) * ca / 100
        self.map = None
        if tv:
            xs = (np.arange(w, dtype=np.float32) + 0.5 - w / 2) / half
            ys = (np.arange(h, dtype=np.float32) + 0.5 - h / 2) / half
            X, Y = np.meshgrid(xs, ys)
            lam = distortion_lambda(tv, w, h)
            s = (1 / (1 + lam * (X * X + Y * Y))).astype(np.float32)
            bx, by = X * s * half, Y * s * half         # de dónde viene cada píxel, en píxeles
            zoom = 1.0
            if fill:
                # acercar lo justo para que ninguna muestra caiga fuera (el «Fit» de Blender)
                edge = lambda a: np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
                zoom = max(1.0, float(np.abs(edge(bx)).max() / (w / 2)),
                           float(np.abs(edge(by)).max() / (h / 2)))
            x = (bx / zoom + w / 2 - 0.5).ravel()
            y = (by / zoom + h / 2 - 0.5).ravel()
            valid = ((x >= -0.5) & (x <= w - 0.5) & (y >= -0.5) & (y <= h - 0.5))
            x = np.clip(x, 0, w - 1)
            y = np.clip(y, 0, h - 1)
            x0 = np.minimum(np.floor(x), max(w - 2, 0))
            y0 = np.minimum(np.floor(y), max(h - 2, 0))
            idx = (y0 * w + x0).astype(np.intp)
            fx, fy = (x - x0).astype(np.float32), (y - y0).astype(np.float32)
            self.map = (idx, fx, fy, None if valid.all() else valid.astype(np.float32))
        self.steps = []
        if steps:
            if fill:
                # se ve con aumento m muestreando en x / m: con todos los aumentos >= 1,
                # ninguna longitud de onda pide nada de fuera de la imagen
                mags = mags / mags.min()
            # cada canal recoge las muestras cercanas a su color (rampas, como en Blender)
            hat = lambda c: np.clip(1 - 2 * np.abs(t - c), 0, 1)
            weights = np.stack([hat(1.0), hat(0.5), hat(0.0)], axis=1)     # R, G, B
            weights /= weights.sum(axis=0)
            for mag, wt in zip(mags, weights):
                self.steps.append((_lerp_table(w, mag), _lerp_table(h, mag), wt.astype(np.float32)))

    def apply(self, src):
        h, w = self.h, self.w
        if self.map is not None:
            src = self._distort(src)
        if self.steps:
            src = self._aberrate(src)
        return src

    def _distort(self, src):
        idx, fx, fy, valid = self.map
        flat = src.reshape(-1, 4)
        out = np.empty_like(flat)
        w = self.w

        def work(s):
            i = idx[s]
            a = flat.take(i, axis=0)
            b = flat.take(i + 1, axis=0)
            c = flat.take(i + w, axis=0)
            d = flat.take(i + w + 1, axis=0)
            f = fx[s, None]
            a += (b - a) * f
            c += (d - c) * f
            a += (c - a) * fy[s, None]
            if valid is not None:
                a *= valid[s, None]
            out[s] = a

        n = flat.shape[0]
        chunk = 16384
        list(_pool.map(lambda k: work(slice(k, k + chunk)), range(0, n, chunk)))
        return out.reshape(src.shape)

    def _aberrate(self, src):
        out = np.zeros_like(src)
        out[..., 3] = src[..., 3]

        def work(rows):
            for (ix, wx, inx), (iy, wy, iny), wt in self.steps:
                ys, fy = iy[rows], wy[rows, None]
                mask = inx * iny[rows, None]
                for c in np.flatnonzero(wt):        # solo los canales a los que llega esta luz
                    plane = src[..., c]
                    a = plane[ys]
                    a += (plane[ys + 1] - a) * fy
                    b = a.take(ix, axis=1)
                    b += (a.take(ix + 1, axis=1) - b) * wx
                    b *= mask * wt[c]
                    out[rows, :, c] += b

        by_bands(self.h, work)
        return out


# ------------------------------------------------------------------ viñeteado

def vignette_gain(w, h, amount, midpoint=0.0, roundness=0.0, fit=0.0):
    """Lo que se multiplica la luz en cada píxel: 1 en el centro y 2^amount en la esquina.

    La caída, 2^(amount·r²), es la de los perfiles medidos de objetivos rápidos abiertos.
    roundness 0 → círculo, 1 → casi un cuadrado con las esquinas redondeadas (superelipse);
    fit 0 → redondo de verdad, como un objetivo, 1 → estirado al encuadre; midpoint deja el
    centro sin tocar hasta esa fracción del radio."""
    half = math.hypot(w, h) / 2
    sx = half + (w / 2 - half) * fit
    sy = half + (h / 2 - half) * fit
    n = 2.0 * 4.0 ** roundness
    x = np.abs((np.arange(w, dtype=np.float32) + 0.5 - w / 2) / sx)[None, :]
    y = np.abs((np.arange(h, dtype=np.float32) + 0.5 - h / 2) / sy)[:, None]
    r = (x ** n + y ** n) ** (1 / n)
    r /= ((w / 2 / sx) ** n + (h / 2 / sy) ** n) ** (1 / n)    # esquina = 1
    if midpoint > 0:
        r = np.clip((r - midpoint) / (1 - midpoint), 0, None)
    return np.exp2(np.float32(amount) * r * r).astype(np.float32)


# ------------------------------------------------------------------ grano y ruido

def _gaussian_noise(h, w, channels, sigma_px, seed):
    """Ruido blanco gaussiano suavizado con una gaussiana de sigma_px, periódico (se puede
    desplazar dando la vuelta sin que se note la costura) y con desviación 1 en cada canal."""
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal((channels, h, w), dtype=np.float32)
    if sigma_px > 0.3:
        fy = np.fft.fftfreq(h)[:, None]
        fx = np.fft.rfftfreq(w)[None, :]
        kernel = np.exp(-2 * (math.pi * sigma_px) ** 2 * (fx * fx + fy * fy)).astype(np.float32)
        noise = np.fft.irfft2(np.fft.rfft2(noise) * kernel, s=(h, w)).astype(np.float32)
        noise /= noise.std(axis=(1, 2), keepdims=True)
    return np.ascontiguousarray(noise.transpose(1, 2, 0))


def film_grain_params(stock, gauge, w):
    """(σ en pasos por canal R G B en la parte recta de la curva, tamaño del grano en píxeles)
    para esa emulsión, ese formato y ese ancho de imagen. σD de Kodak → exposición: / gamma
    del negativo; de los 48 µm del microdensitómetro a un píxel: ley de Selwyn, σ √A constante,
    con el área del píxel más la de una nube de colorante (cuando el píxel se acerca al tamaño
    de la nube, la ley deja de cumplirse y el grano ya no crece igual); y lo que suaviza el
    escáner."""
    sigma_d = np.array(STOCKS[stock][1], np.float32) / 1000
    pixel_um = GAUGES[gauge][1] * 1000 / w
    area = pixel_um ** 2 + math.pi * (DYE_CLOUD_UM / 2) ** 2
    selwyn = math.sqrt(math.pi * (APERTURE_UM / 2) ** 2 / area)
    stops = sigma_d / NEGATIVE_GAMMA * math.log2(10) * selwyn * SCAN_FACTOR
    return stops, (DYE_CLOUD_UM / 2) / pixel_um


def grain_mix(color, correlation=LAYER_CORRELATION):
    """Matriz que reparte el grano de cada capa (o de cada fotodiodo) en R G B. color 1 = lo
    físico (cada uno el suyo, con esa correlación); color 0 = el mismo grano en los tres (gris)."""
    physical = np.full((3, 3), correlation, np.float32)
    np.fill_diagonal(physical, 1.0)
    mono = np.zeros((3, 3), np.float32)
    mono[:, 1] = 1.0
    mix = color * physical + (1 - color) * mono
    return mix / np.sqrt((mix ** 2).sum(axis=1, keepdims=True))


def halation_radius_px(radius_mm, gauge, w):
    return radius_mm / GAUGES[gauge][1] * w


def _blur_wide(img, sigma_px):
    """Desenfoque gaussiano grande y barato: reducir, desenfocar con FFT y volver a ampliar."""
    h, w, c = img.shape
    d = max(1, int(sigma_px / 3))
    hs, ws = max(1, h // d), max(1, w // d)
    small = img[:hs * d, :ws * d].reshape(hs, d, ws, d, c).mean(axis=(1, 3))
    s = sigma_px / d
    # con margen para que lo que sale por un borde no aparezca por el otro
    pad = min(int(3 * s) + 1, hs, ws)
    small = np.pad(small, ((pad, pad), (pad, pad), (0, 0)), mode="edge")
    fy = np.fft.fftfreq(small.shape[0])[:, None]
    fx = np.fft.rfftfreq(small.shape[1])[None, :]
    kernel = np.exp(-2 * (math.pi * s) ** 2 * (fx * fx + fy * fy)).astype(np.float32)
    blurred = np.fft.irfft2(np.fft.rfft2(small, axes=(0, 1)) * kernel[..., None],
                            s=small.shape[:2], axes=(0, 1))[pad:pad + hs, pad:pad + ws].astype(np.float32)
    if d == 1:
        out = np.zeros_like(img)
        out[:hs, :ws] = blurred
        return out
    # ampliar con interpolación lineal (filas y luego columnas)
    def axis_table(n, m):
        x = np.clip((np.arange(n) + 0.5) / d - 0.5, 0, m - 1)
        i0 = np.minimum(np.floor(x).astype(np.intp), max(m - 2, 0))
        return i0, np.minimum(i0 + 1, m - 1), (x - i0).astype(np.float32)
    y0, y1, fy_ = axis_table(h, hs)
    x0, x1, fx_ = axis_table(w, ws)
    rows = blurred[y0] + (blurred[y1] - blurred[y0]) * fy_[:, None, None]
    return rows[:, x0] + (rows[:, x1] - rows[:, x0]) * fx_[None, :, None]


def digital_noise_params(iso, w, h):
    """(a, b) del ruido σ² = a·señal + b en lineal, para el verde: fotones más lectura. El
    ruido de lectura pesa más cuanto más alta es la ISO (en gris medio sigue mandando el de
    fotones)."""
    snr_db = SNR18_ISO100_DB - 10 * math.log10(iso / 100)
    sigma18 = 0.18 / 10 ** (snr_db / 20) * math.sqrt(w * h / 8e6)
    isos, ratios = zip(*READ_RATIO)
    read = float(np.interp(math.log2(iso), np.log2(isos), ratios)) * sigma18
    return (sigma18 ** 2 - read ** 2) / 0.18, read ** 2


def frame_offset(frame, h, w):
    """Desplazamiento del patrón de grano para ese fotograma: siempre el mismo para el mismo
    número, y distinto de un fotograma al siguiente."""
    rng = np.random.default_rng([0x5E9, int(frame) & 0xFFFFFFFF])
    return int(rng.integers(h)), int(rng.integers(w))


# ------------------------------------------------------------------ etalonaje

# sRGB / Rec.709 lineal ↔ XYZ (D65) y la matriz de Bradford para adaptar el blanco
_RGB_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                        [0.2126729, 0.7151522, 0.0721750],
                        [0.0193339, 0.1191920, 0.9503041]])
_XYZ_TO_RGB = np.linalg.inv(_RGB_TO_XYZ)
_BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                      [-0.7502, 1.7135, 0.0367],
                      [0.0389, -0.0685, 1.0296]])
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
NEUTRAL_K = 6500.0


def planckian_xy(kelvin):
    """Color (x, y) de un cuerpo negro a esa temperatura: aproximación de Kim et al. (2002),
    válida de 1667 a 25000 K."""
    t = float(np.clip(kelvin, 1667, 25000))
    if t <= 4000:
        x = -0.2661239e9 / t ** 3 - 0.2343589e6 / t ** 2 + 0.8776956e3 / t + 0.179910
    else:
        x = -3.0258469e9 / t ** 3 + 2.1070379e6 / t ** 2 + 0.2226347e3 / t + 0.240390
    if t <= 2222:
        y = -1.1063814 * x ** 3 - 1.34811020 * x ** 2 + 2.18555832 * x - 0.20219683
    elif t <= 4000:
        y = -0.9549476 * x ** 3 - 1.37418593 * x ** 2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x ** 3 - 5.87338670 * x ** 2 + 3.75112997 * x - 0.37001483
    return x, y


def _white_xyz(kelvin, duv):
    """Blanco de una luz a esa temperatura, desplazado duv en perpendicular al lugar de Planck
    (en el diagrama u'v' de 1960): positivo = más verde."""
    def uv(k):
        x, y = planckian_xy(k)
        d = -2 * x + 12 * y + 3
        return np.array([4 * x / d, 6 * y / d])
    u, v = uv(kelvin)
    tangent = uv(kelvin * 1.01) - uv(kelvin * 0.99)
    normal = np.array([-tangent[1], tangent[0]]) / np.hypot(*tangent)
    if normal[1] < 0:
        normal = -normal                        # que apunte hacia el verde (v mayor)
    u, v = np.array([u, v]) + duv * normal
    x, y = 3 * u / (2 * u - 8 * v + 4), 2 * v / (2 * u - 8 * v + 4)
    return np.array([x / y, 1.0, (1 - x - y) / y])


def white_balance_matrix(kelvin, tint):
    """Matriz RGB lineal del balance de blancos, como en una cámara o en Lightroom: se le dice
    de qué color era la luz y lo corrige (Bradford). A 3200 K la imagen se enfría (compensa
    una bombilla); por encima de 6500 K se calienta. Tinte positivo = hacia magenta."""
    if kelvin == NEUTRAL_K and not tint:
        return None
    src = _BRADFORD @ _white_xyz(kelvin, tint / 100 * 0.02)
    dst = _BRADFORD @ _white_xyz(NEUTRAL_K, 0.0)
    adapt = np.linalg.inv(_BRADFORD) @ np.diag(dst / src) @ _BRADFORD
    return (_XYZ_TO_RGB @ adapt @ _RGB_TO_XYZ).astype(np.float32)


def grade(rgba, exposure=0.0, contrast=1.0, gamma=1.0, temperature=NEUTRAL_K, tint=0.0, saturation=1.0):
    """Balance de blancos, brillo en pasos (multiplica la luz), contraste alrededor del gris
    medio (en logaritmo, como el de un etalonaje: el gris 0,18 no se mueve y el negro sigue en
    negro), gamma (el blanco 1 no se mueve) y saturación (alrededor de la luminancia, que no
    cambia). Se queda en lineal: en un EXR no recorta las luces."""
    wb = white_balance_matrix(temperature, tint)
    if wb is None and not exposure and contrast == 1 and gamma == 1 and saturation == 1:
        return rgba
    gain = np.float32(2.0 ** exposure)
    grey = np.float32(0.18)
    sat = np.float32(saturation)

    def work(rows):
        px = rgba[rows, :, :3]
        if wb is not None:
            px[...] = px @ wb.T
        if exposure:
            px *= gain
        if contrast != 1 or gamma != 1:
            np.maximum(px, 0, out=px)
        if contrast != 1:
            px[...] = grey * np.power(px / grey, np.float32(contrast))
        if gamma != 1:
            np.power(px, np.float32(1 / gamma), out=px)
        if saturation != 1:
            y = (px @ LUMA)[..., None]
            px[...] = y + (px - y) * sat
            np.maximum(px, 0, out=px)

    by_bands(rgba.shape[0], work)
    return rgba


# ------------------------------------------------------------------ todo junto

class Look:
    """Los efectos con sus ajustes y lo que se puede calcular una vez por tamaño de imagen."""

    def __init__(self, settings=None):
        self._lock = threading.Lock()
        self._cache = {}
        self.set(settings)

    def set(self, settings):
        self.settings = merged(settings)

    def active(self):
        return any(self.settings[e]["on"] for e in EFFECTS)

    def key(self):
        """Cambia cuando cambia lo que se ve, para no revelar dos veces lo mismo."""
        return repr(sorted((e, sorted(v.items())) for e, v in self.settings.items() if v["on"]))

    def _cached(self, key, make):
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        value = make()
        with self._lock:
            if len(self._cache) > 8:
                self._cache.clear()
            self._cache[key] = value
        return value

    def apply(self, rgba, frame=0):
        """Aplica los efectos encendidos a un array float32 (alto, ancho, 4) lineal sin
        premultiplicar. Devuelve el resultado (puede ser el mismo array, modificado)."""
        s = self.settings
        h, w = rgba.shape[:2]
        dist, ab = s["distortion"], s["aberration"]
        tv = dist["amount"] if dist["on"] else 0.0
        ca = ab["amount"] if ab["on"] else 0.0
        if tv or ca:
            warp = self._cached(("warp", w, h, tv, ca, dist["fill"]),
                                lambda: _Warp(w, h, tv, ca, dist["fill"]))
            rgba = warp.apply(rgba)
        v = s["vignette"]
        if v["on"] and v["amount"]:
            gain = self._cached(("vig", w, h, v["amount"], v["midpoint"], v["roundness"], v["fit"]),
                                lambda: vignette_gain(w, h, v["amount"], v["midpoint"], v["roundness"], v["fit"]))

            def vignette(rows):
                rgba[rows, :, :3] *= gain[rows, :, None]
            by_bands(h, vignette)
        hal = s["halation"]
        if hal["on"] and hal["amount"]:
            self._halation(rgba, hal, s["grain"]["gauge"] if s["grain"]["kind"] == "film" else "35")
        g = s["grain"]
        if g["on"] and g["amount"]:
            self._grain(rgba, g, frame)
        if s["grade"]["on"]:
            grade(rgba, **{k: v for k, v in s["grade"].items() if k != "on"})
        return rgba

    def _halation(self, rgba, hal, gauge):
        """Lo que pasa del umbral se desenfoca y vuelve teñido de naranja-rojo."""
        h, w = rgba.shape[:2]
        threshold = 0.18 * 2.0 ** hal["threshold"]
        lum = rgba[..., :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        source = np.maximum(lum - threshold, 0)[..., None]
        if not source.any():
            return
        glow = _blur_wide(source, halation_radius_px(hal["radius"], gauge, w))[..., 0]
        tint = np.array(HALATION_TINT, np.float32) * np.float32(hal["amount"])

        def work(rows):
            rgba[rows, :, :3] += glow[rows, :, None] * tint
        by_bands(h, work)

    def _grain(self, rgba, g, frame):
        if g["kind"] == "film":
            self._film_grain(rgba, g, frame)
        else:
            self._sensor_noise(rgba, g, frame)

    def _shifted(self, plate, rows, dy, dx):
        h = plate.shape[0]
        n = plate[(np.arange(rows.start, min(rows.stop, h)) + dy) % h]
        return np.concatenate([n[:, dx:], n[:, :dx]], axis=1)

    def _film_grain(self, rgba, g, frame):
        h, w = rgba.shape[:2]
        stops, size = film_grain_params(g["stock"], g["gauge"], w)
        plate = self._cached(("plate", w, h, round(size, 3)), lambda: _gaussian_noise(h, w, 3, size, 7))
        mix = grain_mix(float(np.clip(g["color"], 0, 1)))
        scale = (stops * float(g["amount"])).astype(np.float32)
        dy, dx = frame_offset(frame, h, w)
        toe = np.float32(0.18 * 2.0 ** -TOE_STOPS)

        def work(rows):
            n = self._shifted(plate, rows, dy, dx) @ mix.T
            px = rgba[rows, :, :3]
            # el grano está en la densidad del negativo: multiplica la exposición; en el pie
            # de la curva (sombras muy bajas) la curva se aplana y el grano crece
            boost = np.minimum(1 + toe / np.maximum(px, 1e-6), TOE_MAX_BOOST)
            px *= np.exp2(n * scale * boost)

        by_bands(h, work)

    def _sensor_noise(self, rgba, g, frame):
        """Ruido de luminancia de grano fino (algo correlado por el demosaico) más ruido de
        color; la reducción de ruido del móvil o de la cámara de acción baja la luminancia y
        convierte el color en manchas más grandes y más suaves."""
        h, w = rgba.shape[:2]
        nr = float(np.clip(g.get("nr", 0.0), 0, 1))
        chroma_size = 0.5 + 3.0 * nr
        lum = self._cached(("lum", w, h), lambda: _gaussian_noise(h, w, 1, 0.5, 11))
        chroma = self._cached(("chroma", w, h, chroma_size), lambda: _gaussian_noise(h, w, 3, chroma_size, 13))
        k = float(np.clip(g["color"], 0, 1))
        wl = np.float32(math.sqrt(1 - k) * (1 - 0.4 * nr))
        wc = np.float32(math.sqrt(k) * (1 - 0.3 * nr))
        gain = np.array(SENSOR_CHANNEL_GAIN, np.float32)
        a, b = digital_noise_params(g["iso"], w, h)
        amount = float(g["amount"]) ** 2
        a, b = np.float32(a * amount), np.float32(b * amount)
        dy, dx = frame_offset(frame, h, w)

        def work(rows):
            n = (self._shifted(lum, rows, dy, dx) * wl + self._shifted(chroma, rows, dy, dx) * wc) * gain
            px = rgba[rows, :, :3]
            px += np.sqrt(np.maximum(px, 0) * a + b) * n
            np.maximum(px, 0, out=px)

        by_bands(h, work)
