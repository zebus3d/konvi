"""Los efectos de cámara del visor en la gráfica, con los mismos resultados que look.py (el
seqview_gpu.py de dolphin-video-tools, con otra cabecera).

La gráfica se abre en un proceso aparte, con EGL sobre el dispositivo (EGL_EXT_platform_device):
sin ventana ni servidor gráfico, así que funciona igual en Wayland, y se elige qué gráfica
usar (la NVIDIA de un portátil sin despertar nada de X ni PRIME). Si el driver se cae, se cae
ese proceso y el visor sigue con numpy. Los fotogramas van y vuelven por memoria compartida.

    python3 -m konvi.core.gpu --list [--medir]  las gráficas, de la más rápida a la más lenta, en JSON
    python3 -m konvi.core.gpu --serve auto      el proceso que hace los efectos (lo lanza Engine)

Cada efecto es un compute shader (OpenGL 4.3) que repite la cuenta de look.py paso a paso:
lo que cambia son los redondeos del float32 de la gráfica, por debajo de lo que se ve.
"""

import ctypes as C
import json
import math
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import look as fx

# cómo se lanza el proceso de la gráfica: como módulo, con konvi en la ruta de Python
WORKER = [sys.executable, "-m", "konvi.core.gpu"]
PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])

IDLE_EXIT_S = 180           # sin trabajo, el proceso se cierra y la gráfica puede dormirse
REPLY_S = 30                # el primer fotograma genera las placas de grano: tarda algo más

# ------------------------------------------------------------------ EGL y OpenGL por ctypes

EGL_EXTENSIONS = 0x3055
EGL_NONE = 0x3038
EGL_PLATFORM_DEVICE_EXT = 0x313F
EGL_DRM_DEVICE_FILE_EXT = 0x3233
EGL_DRM_RENDER_NODE_FILE_EXT = 0x3377
EGL_OPENGL_API = 0x30A2
EGL_SURFACE_TYPE, EGL_PBUFFER_BIT = 0x3033, 0x0001
EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT = 0x3040, 0x0008
EGL_CONTEXT_MAJOR_VERSION, EGL_CONTEXT_MINOR_VERSION = 0x3098, 0x30FB
EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CORE_BIT = 0x30FD, 0x1

GL_RENDERER = 0x1F01
GL_TEXTURE_2D, GL_TEXTURE_3D = 0x0DE1, 0x806F
GL_RGBA32F, GL_RGBA16F, GL_RGBA16, GL_RGBA8, GL_RG32UI = 0x8814, 0x881A, 0x805B, 0x8058, 0x823C
GL_RGBA, GL_RG_INTEGER = 0x1908, 0x8228
GL_FLOAT, GL_HALF_FLOAT, GL_UNSIGNED_BYTE, GL_UNSIGNED_SHORT, GL_UNSIGNED_INT = 0x1406, 0x140B, 0x1401, 0x1403, 0x1405
# cómo llega cada tipo de fotograma: formato de la textura y tipo de los datos
SOURCE_FORMATS = {"uint8": (GL_RGBA8, GL_UNSIGNED_BYTE), "uint16": (GL_RGBA16, GL_UNSIGNED_SHORT),
                  "float16": (GL_RGBA16F, GL_HALF_FLOAT), "float32": (GL_RGBA32F, GL_FLOAT)}
GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_NEAREST = 0x2801, 0x2800, 0x2600
GL_TEXTURE0 = 0x84C0
GL_WRITE_ONLY, GL_READ_WRITE = 0x88B9, 0x88BA
GL_COMPUTE_SHADER = 0x91B9
GL_COMPILE_STATUS, GL_LINK_STATUS = 0x8B81, 0x8B82
GL_PACK_ALIGNMENT, GL_UNPACK_ALIGNMENT = 0x0D05, 0x0CF5
GL_ALL_BARRIER_BITS = 0xFFFFFFFF

_u, _i, _f, _p = C.c_uint, C.c_int, C.c_float, C.c_void_p


class _Lib:
    """Las funciones de una biblioteca con sus tipos; las que no exporta, por GetProcAddress."""

    def __init__(self, path, getproc=None):
        self.lib = C.CDLL(path)
        self.getproc = getproc

    def fn(self, name, res, *args):
        try:
            f = getattr(self.lib, name)
            f.restype, f.argtypes = res, list(args)
            return f
        except AttributeError:
            addr = self.getproc(name.encode()) if self.getproc else None
            if not addr:
                raise
            return C.CFUNCTYPE(res, *args)(addr)


class EGL:
    def __init__(self):
        lib = _Lib("libEGL.so.1")
        self.GetProcAddress = lib.fn("eglGetProcAddress", _p, C.c_char_p)
        lib.getproc = self.GetProcAddress
        self.Initialize = lib.fn("eglInitialize", _u, _p, C.POINTER(_i), C.POINTER(_i))
        self.Terminate = lib.fn("eglTerminate", _u, _p)
        self.BindAPI = lib.fn("eglBindAPI", _u, _u)
        self.ChooseConfig = lib.fn("eglChooseConfig", _u, _p, C.POINTER(_i), C.POINTER(_p), _i, C.POINTER(_i))
        self.CreateContext = lib.fn("eglCreateContext", _p, _p, _p, _p, C.POINTER(_i))
        self.MakeCurrent = lib.fn("eglMakeCurrent", _u, _p, _p, _p, _p)
        self.QueryDevices = lib.fn("eglQueryDevicesEXT", _u, _i, C.POINTER(_p), C.POINTER(_i))
        self.QueryDeviceString = lib.fn("eglQueryDeviceStringEXT", C.c_char_p, _p, _i)
        self.GetPlatformDisplay = lib.fn("eglGetPlatformDisplayEXT", _p, _u, _p, C.POINTER(_i))

    def devices(self):
        n = _i()
        devs = (_p * 32)()
        if not self.QueryDevices(32, devs, C.byref(n)):
            return []
        out = []
        for d in devs[:n.value]:
            ext = (self.QueryDeviceString(d, EGL_EXTENSIONS) or b"").decode()
            if "EGL_MESA_device_software" in ext:
                continue                    # llvmpipe: numpy en el procesador va igual o mejor
            node = self.QueryDeviceString(d, EGL_DRM_RENDER_NODE_FILE_EXT)
            out.append((d, node.decode() if node else ""))
        return out

    def open(self, device):
        """Contexto OpenGL 4.3 core sin superficie en ese dispositivo, o None."""
        dpy = self.GetPlatformDisplay(EGL_PLATFORM_DEVICE_EXT, device, None)
        major, minor = _i(), _i()
        if not dpy or not self.Initialize(dpy, C.byref(major), C.byref(minor)):
            return None
        self.BindAPI(EGL_OPENGL_API)
        cfg, count = _p(), _i()
        attrs = (_i * 5)(EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT, EGL_NONE)
        if not self.ChooseConfig(dpy, attrs, C.byref(cfg), 1, C.byref(count)) or not count.value:
            cfg = _p()                      # EGL_KHR_no_config_context
        ctx_attrs = (_i * 7)(EGL_CONTEXT_MAJOR_VERSION, 4, EGL_CONTEXT_MINOR_VERSION, 3,
                             EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CORE_BIT, EGL_NONE)
        ctx = self.CreateContext(dpy, cfg, None, ctx_attrs)
        if not ctx or not self.MakeCurrent(dpy, None, None, ctx):
            self.Terminate(dpy)
            return None
        return dpy


def display_gpu(node):
    """La gráfica que arrancó la pantalla (boot_vga): en un portátil, la integrada."""
    try:
        return Path(f"/sys/class/drm/{Path(node).name}/device/boot_vga").read_text().strip() == "1"
    except OSError:
        return False


class GL:
    def __init__(self, egl):
        lib = _Lib("libOpenGL.so.0", egl.GetProcAddress)
        fns = {
            "GetString": (C.c_char_p, _u), "GetError": (_u,),
            "CreateShader": (_u, _u), "ShaderSource": (None, _u, _i, C.POINTER(C.c_char_p), _p),
            "CompileShader": (None, _u), "GetShaderiv": (None, _u, _u, C.POINTER(_i)),
            "GetShaderInfoLog": (None, _u, _i, _p, C.c_char_p), "DeleteShader": (None, _u),
            "CreateProgram": (_u,), "AttachShader": (None, _u, _u), "LinkProgram": (None, _u),
            "GetProgramiv": (None, _u, _u, C.POINTER(_i)),
            "GetProgramInfoLog": (None, _u, _i, _p, C.c_char_p), "UseProgram": (None, _u),
            "GetUniformLocation": (_i, _u, C.c_char_p),
            "Uniform1i": (None, _i, _i), "Uniform1f": (None, _i, _f), "Uniform2i": (None, _i, _i, _i),
            "Uniform2f": (None, _i, _f, _f), "Uniform3f": (None, _i, _f, _f, _f),
            "Uniform1fv": (None, _i, _i, _p), "Uniform3fv": (None, _i, _i, _p),
            "UniformMatrix3fv": (None, _i, _i, C.c_ubyte, _p),
            "GenTextures": (None, _i, C.POINTER(_u)), "DeleteTextures": (None, _i, C.POINTER(_u)),
            "BindTexture": (None, _u, _u), "ActiveTexture": (None, _u),
            "TexStorage2D": (None, _u, _i, _u, _i, _i), "TexStorage3D": (None, _u, _i, _u, _i, _i, _i),
            "TexSubImage2D": (None, _u, _i, _i, _i, _i, _i, _u, _u, _p),
            "TexSubImage3D": (None, _u, _i, _i, _i, _i, _i, _i, _i, _u, _u, _p),
            "TexParameteri": (None, _u, _u, _i), "PixelStorei": (None, _u, _i),
            "GetTexImage": (None, _u, _i, _u, _u, _p),
            "BindImageTexture": (None, _u, _u, _i, C.c_ubyte, _i, _u, _u),
            "DispatchCompute": (None, _u, _u, _u), "MemoryBarrier": (None, _u),
            "Flush": (None,), "Finish": (None,),
        }
        for name, (res, *args) in fns.items():
            setattr(self, name, lib.fn("gl" + name, res, *args))


# ------------------------------------------------------------------ shaders

_HEAD = """#version 430
layout(local_size_x = 16, local_size_y = 16) in;
const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
const float EDGE = 1e-3;            // lo que puede pasarse del borde por redondeo una muestra que cae en él
uniform ivec2 size;                 // el tamaño de lo que se escribe
#define PIXEL ivec2 p = ivec2(gl_GlobalInvocationID.xy); if (p.x >= size.x || p.y >= size.y) return;

// ampliar al doble (_up2 de look.py): 3/4 del píxel de abajo y 1/4 del vecino más cercano
vec3 up2(sampler2D s, ivec2 p, ivec2 m) {
    ivec2 i = min(p / 2, m - 1);
    ivec2 nb = clamp(i + ivec2((p.x & 1) == 1 ? 1 : -1, (p.y & 1) == 1 ? 1 : -1), ivec2(0), m - 1);
    vec3 r0 = texelFetch(s, i, 0).rgb * 0.75 + texelFetch(s, ivec2(i.x, nb.y), 0).rgb * 0.25;
    vec3 r1 = texelFetch(s, ivec2(nb.x, i.y), 0).rgb * 0.75 + texelFetch(s, nb, 0).rgb * 0.25;
    return r0 * 0.75 + r1 * 0.25;
}

// volver a tamaño completo desde una imagen reducida d veces (el final de _blur_wide)
vec3 upsample(sampler2D s, ivec2 p, int d, ivec2 m) {
    vec2 x = clamp((vec2(p) + 0.5) / float(d) - 0.5, vec2(0.0), vec2(m - 1));
    ivec2 i0 = min(ivec2(floor(x)), max(m - 2, 0));
    ivec2 i1 = min(i0 + 1, m - 1);
    vec2 f = x - vec2(i0);
    vec3 a = texelFetch(s, i0, 0).rgb, b = texelFetch(s, ivec2(i0.x, i1.y), 0).rgb;
    vec3 c = texelFetch(s, ivec2(i1.x, i0.y), 0).rgb, e = texelFetch(s, i1, 0).rgb;
    vec3 r0 = a + (b - a) * f.y, r1 = c + (e - c) * f.y;
    return r0 + (r1 - r0) * f.x;
}

vec3 encode(vec3 v) {
    v = max(v, 0.0);
    return mix(1.055 * pow(v, vec3(1.0 / 2.4)) - 0.055, v * 12.92, lessThanEqual(v, vec3(0.0031308)));
}

vec3 decode(vec3 v) {
    v = max(v, 0.0);
    return mix(pow((v + 0.055) / 1.055, vec3(2.4)), v / 12.92, lessThanEqual(v, vec3(0.04045)));
}
"""

SHADERS = {
    # distorsión: de dónde viene cada píxel y remuestreo bilineal (_Warp._distort)
    "distort": """
layout(binding = 0) uniform sampler2D src;
layout(rgba32f, binding = 0) uniform writeonly image2D dst;
uniform float half_diag, lam, zoom;
void main() {
    PIXEL
    vec2 c = (vec2(p) + 0.5 - vec2(size) / 2.0) / half_diag;
    vec2 b = c * (1.0 / (1.0 + lam * dot(c, c))) * half_diag;
    vec2 q = b / zoom + vec2(size) / 2.0 - 0.5;
    // con un margen de redondeo: con «fill», la esquina cae justo en el borde
    bool valid = all(greaterThanEqual(q, vec2(-0.5 - EDGE))) && all(lessThanEqual(q, vec2(size) - 0.5 + EDGE));
    q = clamp(q, vec2(0.0), vec2(size - 1));
    ivec2 i = min(ivec2(floor(q)), max(size - 2, 0));
    vec2 f = q - vec2(i);
    vec4 a = texelFetch(src, i, 0), e = texelFetch(src, i + ivec2(1, 0), 0);
    vec4 g = texelFetch(src, i + ivec2(0, 1), 0), h = texelFetch(src, i + 1, 0);
    a += (e - a) * f.x;
    g += (h - g) * f.x;
    a += (g - a) * f.y;
    imageStore(dst, p, valid ? a : vec4(0.0));
}""",
    # aberración: cada longitud de onda, la imagen con otro aumento (_Warp._aberrate)
    "aberrate": """
layout(binding = 0) uniform sampler2D src;
layout(rgba32f, binding = 0) uniform writeonly image2D dst;
uniform int steps;
uniform float mags[9];
uniform vec3 wts[9];
void main() {
    PIXEL
    vec3 acc = vec3(0.0);
    for (int k = 0; k < steps; k++) {
        vec2 q = (vec2(p) + 0.5 - vec2(size) / 2.0) / mags[k] + vec2(size) / 2.0 - 0.5;
        bool inside = all(greaterThanEqual(q, vec2(-0.5 - EDGE))) && all(lessThanEqual(q, vec2(size) - 0.5 + EDGE));
        q = clamp(q, vec2(0.0), vec2(size - 1));
        ivec2 i = min(ivec2(floor(q)), max(size - 2, 0));
        vec2 f = q - vec2(i);
        vec3 a = texelFetch(src, i, 0).rgb, e = texelFetch(src, i + ivec2(1, 0), 0).rgb;
        vec3 g = texelFetch(src, i + ivec2(0, 1), 0).rgb, h = texelFetch(src, i + 1, 0).rgb;
        a += (g - a) * f.y;
        e += (h - e) * f.y;
        a += (e - a) * f.x;
        if (inside)
            acc += a * wts[k];
    }
    imageStore(dst, p, vec4(acc, texelFetch(src, p, 0).a));
}""",
    # glow: media resolución con la media de Karis y el umbral con rodilla (glow_half)
    "karis": """
layout(binding = 0) uniform sampler2D src;
layout(rgba32f, binding = 0) uniform writeonly image2D dst;
uniform ivec2 src_size;
uniform float t, k;
void main() {
    PIXEL
    ivec2 a = 2 * p, b = min(a + 1, src_size - 1);
    vec3 c[4] = vec3[4](texelFetch(src, a, 0).rgb, texelFetch(src, ivec2(a.x, b.y), 0).rgb,
                        texelFetch(src, ivec2(b.x, a.y), 0).rgb, texelFetch(src, b, 0).rgb);
    vec3 num = vec3(0.0);
    float den = 0.0;
    for (int j = 0; j < 4; j++) {
        float w = 1.0 / (1.0 + max(dot(c[j], LUMA), 0.0));
        num += c[j] * w;
        den += w;
    }
    vec3 v = num * 0.25 / max(den * 0.25, 1e-6);
    float bright = max(max(v.r, v.g), v.b);
    float soft = clamp(bright - t + k, 0.0, 2.0 * k);
    soft = soft * soft / (4.0 * k);
    v *= max(soft, bright - t) / max(bright, 1e-6);
    imageStore(dst, p, vec4(v, 0.0));
}""",
    # [1 2 1]/4 en una dirección; con down, sobre la imagen reducida a la mitad (_blur121, _down2)
    "blur121": """
layout(binding = 0) uniform sampler2D src;
layout(rgba32f, binding = 0) uniform writeonly image2D dst;
uniform ivec2 src_size, dir;
uniform int down;
vec3 at(ivec2 q) {
    q = clamp(q, ivec2(0), size - 1);
    if (down == 0)
        return texelFetch(src, q, 0).rgb;
    ivec2 a = 2 * q, b = min(a + 1, src_size - 1);
    return (texelFetch(src, a, 0).rgb + texelFetch(src, ivec2(a.x, b.y), 0).rgb
            + texelFetch(src, ivec2(b.x, a.y), 0).rgb + texelFetch(src, b, 0).rgb) * 0.25;
}
void main() {
    PIXEL
    imageStore(dst, p, vec4((at(p) * 2.0 + at(p - dir) + at(p + dir)) * 0.25, 0.0));
}""",
    # sube un nivel de la pirámide y suma el de ese tamaño con su peso
    "up_add": """
layout(binding = 0) uniform sampler2D src;
layout(binding = 1) uniform sampler2D level;
layout(rgba32f, binding = 0) uniform writeonly image2D dst;
uniform ivec2 src_size;
uniform float weight, src_scale, saturation;
uniform int saturate;
void main() {
    PIXEL
    vec3 c = texelFetch(level, p, 0).rgb * weight + up2(src, p, src_size) * src_scale;
    if (saturate == 1) {
        float y = dot(c, LUMA);
        c = max(y + (c - y) * saturation, 0.0);
    }
    imageStore(dst, p, vec4(c, 0.0));
}""",
    "glow_add": """
layout(binding = 0) uniform sampler2D glow;
layout(rgba32f, binding = 0) uniform image2D img;
uniform ivec2 src_size;
uniform float amount;
uniform int only;
void main() {
    PIXEL
    vec4 px = imageLoad(img, p);
    vec3 g = up2(glow, p, src_size) * amount;
    px.rgb = only == 1 ? g : px.rgb + g;
    imageStore(img, p, px);
}""",
    "vignette": """
layout(rgba32f, binding = 0) uniform image2D img;
uniform vec2 scale;
uniform float n, corner, midpoint, amount;
void main() {
    PIXEL
    vec2 xy = abs((vec2(p) + 0.5 - vec2(size) / 2.0) / scale);
    float r = pow(pow(xy.x, n) + pow(xy.y, n), 1.0 / n) / corner;
    if (midpoint > 0.0)
        r = max((r - midpoint) / (1.0 - midpoint), 0.0);
    vec4 px = imageLoad(img, p);
    px.rgb *= exp2(amount * r * r);
    imageStore(img, p, px);
}""",
    # reducir d veces con la media de cada bloque (el principio de _blur_wide); mode 1 = halación
    "box_down": """
layout(binding = 0) uniform sampler2D src;
layout(rgba32f, binding = 0) uniform writeonly image2D dst;
uniform int d, mode;
uniform float threshold;
void main() {
    PIXEL
    vec3 acc = vec3(0.0);
    for (int y = 0; y < d; y++)
        for (int x = 0; x < d; x++) {
            vec3 c = texelFetch(src, p * d + ivec2(x, y), 0).rgb;
            acc += mode == 1 ? vec3(max(dot(c, LUMA) - threshold, 0.0)) : c;
        }
    imageStore(dst, p, vec4(acc / float(d * d), 0.0));
}""",
    "gauss": """
layout(binding = 0) uniform sampler2D src;
layout(rgba32f, binding = 0) uniform writeonly image2D dst;
uniform ivec2 dir;
uniform int radius;
uniform float k[64];
void main() {
    PIXEL
    vec3 acc = vec3(0.0);
    for (int j = -radius; j <= radius; j++)
        acc += texelFetch(src, clamp(p + dir * j, ivec2(0), size - 1), 0).rgb * k[j + radius];
    imageStore(dst, p, vec4(acc, 0.0));
}""",
    "halation_add": """
layout(binding = 0) uniform sampler2D glow;
layout(rgba32f, binding = 0) uniform image2D img;
uniform ivec2 src_size;
uniform int d;
uniform vec3 tint;
void main() {
    PIXEL
    vec4 px = imageLoad(img, p);
    px.rgb += upsample(glow, p, d, src_size).r * tint;
    imageStore(img, p, px);
}""",
    "film_grain": """
layout(binding = 0) uniform sampler2D plate;
layout(rgba32f, binding = 0) uniform image2D img;
uniform ivec2 offset;
uniform mat3 mixm;
uniform vec3 scale;
uniform float toe, max_boost;
void main() {
    PIXEL
    vec3 n = mixm * texelFetch(plate, (p + offset) % size, 0).rgb;
    vec4 px = imageLoad(img, p);
    vec3 boost = min(1.0 + toe / max(px.rgb, 1e-6), max_boost);
    px.rgb *= exp2(n * scale * boost);
    imageStore(img, p, px);
}""",
    "sensor_noise": """
layout(binding = 0) uniform sampler2D lum;
layout(binding = 1) uniform sampler2D chroma;
layout(rgba32f, binding = 0) uniform image2D img;
uniform ivec2 offset;
uniform float wl, wc, a, b;
uniform vec3 gain;
void main() {
    PIXEL
    ivec2 q = (p + offset) % size;
    vec3 n = (texelFetch(lum, q, 0).r * wl + texelFetch(chroma, q, 0).rgb * wc) * gain;
    vec4 px = imageLoad(img, p);
    px.rgb = max(px.rgb + sqrt(max(px.rgb, 0.0) * a + b) * n, 0.0);
    imageStore(img, p, px);
}""",
    "grade": """
layout(rgba32f, binding = 0) uniform image2D img;
uniform int use_wb, use_gain, use_contrast, use_gamma, use_sat;
uniform mat3 wb;
uniform float gain, contrast, inv_gamma, saturation;
void main() {
    PIXEL
    vec4 px = imageLoad(img, p);
    vec3 c = px.rgb;
    if (use_wb == 1) c = wb * c;
    if (use_gain == 1) c *= gain;
    if (use_contrast == 1 || use_gamma == 1) c = max(c, 0.0);
    if (use_contrast == 1) c = 0.18 * pow(c / 0.18, vec3(contrast));
    if (use_gamma == 1) c = pow(c, vec3(inv_gamma));
    if (use_sat == 1) {
        float y = dot(c, LUMA);
        c = max(y + (c - y) * saturation, 0.0);
    }
    imageStore(img, p, vec4(c, px.a));
}""",
    "highlights": """
layout(rgba32f, binding = 0) uniform image2D img;
uniform float knee, a;
void main() {
    PIXEL
    vec4 px = imageLoad(img, p);
    vec3 t = px.rgb - knee;
    px.rgb = mix(px.rgb, knee + t / (1.0 + t / a), greaterThan(t, vec3(0.0)));
    imageStore(img, p, px);
}""",
    # los retoques sobre los valores sRGB: equilibrio, curvas y LUT; luego, o se vuelve a
    # lineal aquí mismo, o se deja en enc para el enfoque
    "display": """
layout(binding = 0) uniform sampler2D curves;
layout(binding = 1) uniform sampler2D lut1;
layout(binding = 2) uniform sampler3D lut3;
layout(rgba32f, binding = 0) uniform image2D img;
layout(rgba32f, binding = 1) uniform writeonly image2D enc_out;
uniform int use_balance, use_curves, lut_kind, lut_n, to_enc;
uniform vec3 shadows, midtones, lights;
uniform int curve_n;
uniform vec3 end_y, end_slope;
uniform vec3 lut_lo, lut_hi;
uniform float lut_mix;
void main() {
    PIXEL
    vec4 px = imageLoad(img, p);
    vec3 e = encode(px.rgb);
    if (use_balance == 1) {
        float l = clamp(dot(e, LUMA), 0.0, 1.0);
        float sh = (1.0 - l) * (1.0 - l), li = l * l;
        e = max(e + sh * shadows + (1.0 - sh - li) * midtones + li * lights, 0.0);
    }
    if (use_curves == 1) {
        vec3 t = clamp(e, 0.0, 1.0) * float(curve_n);
        ivec3 i = min(ivec3(t), curve_n - 1);
        vec3 out_;
        for (int c = 0; c < 3; c++) {
            float a = texelFetch(curves, ivec2(i[c], 0), 0)[c];
            float b = texelFetch(curves, ivec2(i[c] + 1, 0), 0)[c];
            out_[c] = e[c] > 1.0 ? end_y[c] + end_slope[c] * (e[c] - 1.0) : a + (b - a) * (t[c] - float(i[c]));
        }
        e = out_;
    }
    if (lut_kind != 0) {
        vec3 x = clamp((e - lut_lo) / (lut_hi - lut_lo), 0.0, 1.0) * float(lut_n - 1);
        ivec3 i = min(ivec3(x), lut_n - 2);
        vec3 f = x - vec3(i), out_;
        if (lut_kind == 1) {
            for (int c = 0; c < 3; c++) {
                float a = texelFetch(lut1, ivec2(i[c], 0), 0)[c];
                out_[c] = a + (texelFetch(lut1, ivec2(i[c] + 1, 0), 0)[c] - a) * f[c];
            }
        } else {
            vec3 g[4];
            for (int j = 0; j < 4; j++) {
                ivec3 q = i + ivec3(0, j & 1, j >> 1);
                vec3 a = texelFetch(lut3, q, 0).rgb;
                g[j] = a + (texelFetch(lut3, q + ivec3(1, 0, 0), 0).rgb - a) * f.r;
            }
            vec3 g0 = g[0] + (g[1] - g[0]) * f.g, g1 = g[2] + (g[3] - g[2]) * f.g;
            out_ = g0 + (g1 - g0) * f.b;
        }
        e = lut_mix == 1.0 ? out_ : e + (out_ - e) * lut_mix;
    }
    if (to_enc == 1)
        imageStore(enc_out, p, vec4(e, 0.0));
    else
        imageStore(img, p, vec4(decode(e), px.a));
}""",
    # el fotograma tal como viene del fichero a lineal: 8 o 16 bits en sRGB, o ya lineal (linear_rgba)
    "source": """
layout(binding = 0) uniform sampler2D src;
layout(rgba32f, binding = 0) uniform writeonly image2D dst;
uniform int srgb;
void main() {
    PIXEL
    vec4 c = texelFetch(src, p, 0);
    if (srgb == 1)
        c.rgb = mix(pow((c.rgb + 0.055) / 1.055, vec3(2.4)), c.rgb / 12.92, lessThanEqual(c.rgb, vec3(0.04045)));
    imageStore(dst, p, c);
}""",
    # a half, empaquetado en enteros: bajarlo es copiar bytes, sin que el driver convierta
    "to_half": """
layout(binding = 0) uniform sampler2D src;
layout(rg32ui, binding = 0) uniform writeonly uimage2D dst;
void main() {
    PIXEL
    vec4 c = texelFetch(src, p, 0);
    imageStore(dst, p, uvec4(packHalf2x16(c.rg), packHalf2x16(c.ba), 0u, 0u));
}""",
    "sharpen": """
layout(binding = 0) uniform sampler2D enc;
layout(binding = 1) uniform sampler2D blurred;
layout(rgba32f, binding = 0) uniform image2D img;
uniform ivec2 src_size;
uniform int d;
uniform float amount;
void main() {
    PIXEL
    vec3 e = texelFetch(enc, p, 0).rgb;
    e = max(e + (e - upsample(blurred, p, d, src_size)) * amount, 0.0);
    vec4 px = imageLoad(img, p);
    imageStore(img, p, vec4(decode(e), px.a));
}""",
}


class _Program:
    def __init__(self, gl, name, body):
        self.gl = gl
        src = (_HEAD + body).encode()
        sh = gl.CreateShader(GL_COMPUTE_SHADER)
        gl.ShaderSource(sh, 1, (C.c_char_p * 1)(src), None)
        gl.CompileShader(sh)
        ok = _i()
        gl.GetShaderiv(sh, GL_COMPILE_STATUS, C.byref(ok))
        if not ok.value:
            log = C.create_string_buffer(8192)
            gl.GetShaderInfoLog(sh, 8192, None, log)
            raise RuntimeError(f"shader {name}: {log.value.decode(errors='replace')}")
        self.id = gl.CreateProgram()
        gl.AttachShader(self.id, sh)
        gl.LinkProgram(self.id)
        gl.GetProgramiv(self.id, GL_LINK_STATUS, C.byref(ok))
        gl.DeleteShader(sh)
        if not ok.value:
            log = C.create_string_buffer(8192)
            gl.GetProgramInfoLog(self.id, 8192, None, log)
            raise RuntimeError(f"programa {name}: {log.value.decode(errors='replace')}")
        self.locs = {}

    def set(self, name, value):
        gl = self.gl
        loc = self.locs.get(name)
        if loc is None:
            loc = self.locs[name] = gl.GetUniformLocation(self.id, name.encode())
        if loc < 0:
            return
        if isinstance(value, np.ndarray):
            a = np.ascontiguousarray(value, np.float32)
            if a.shape == (3, 3):
                gl.UniformMatrix3fv(loc, 1, 1, a.ctypes.data)      # por filas, como en numpy
            elif a.ndim == 2:
                gl.Uniform3fv(loc, a.shape[0], a.ctypes.data)
            else:
                gl.Uniform1fv(loc, a.size, a.ctypes.data)
        elif isinstance(value, tuple):
            if all(isinstance(v, int) for v in value):
                gl.Uniform2i(loc, *value)
            elif len(value) == 2:
                gl.Uniform2f(loc, *map(float, value))
            else:
                gl.Uniform3f(loc, *map(float, value))
        elif isinstance(value, (bool, int)):
            gl.Uniform1i(loc, int(value))
        else:
            gl.Uniform1f(loc, float(value))


class _Texture:
    def __init__(self, gl, w, h, depth=0, fmt=GL_RGBA32F):
        self.gl, self.w, self.h, self.depth, self.fmt = gl, w, h, depth, fmt
        self.target = GL_TEXTURE_3D if depth else GL_TEXTURE_2D
        tid = _u()
        gl.GenTextures(1, C.byref(tid))
        self.id = tid.value
        gl.BindTexture(self.target, self.id)
        if depth:
            gl.TexStorage3D(self.target, 1, fmt, w, h, depth)
        else:
            gl.TexStorage2D(self.target, 1, fmt, w, h)
        gl.TexParameteri(self.target, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        gl.TexParameteri(self.target, GL_TEXTURE_MAG_FILTER, GL_NEAREST)

    @property
    def size(self):
        return (self.w, self.h)

    def upload(self, rgba):
        """Un array RGBA contiguo del tipo que corresponde al formato (SOURCE_FORMATS)."""
        a = np.ascontiguousarray(rgba)
        kind = SOURCE_FORMATS[a.dtype.name][1]
        self.gl.BindTexture(self.target, self.id)
        if self.depth:
            self.gl.TexSubImage3D(self.target, 0, 0, 0, 0, self.w, self.h, self.depth, GL_RGBA, kind, a.ctypes.data)
        else:
            self.gl.TexSubImage2D(self.target, 0, 0, 0, self.w, self.h, GL_RGBA, kind, a.ctypes.data)

    def download(self, out):
        self.gl.BindTexture(self.target, self.id)
        if self.fmt == GL_RG32UI:
            self.gl.GetTexImage(self.target, 0, GL_RG_INTEGER, GL_UNSIGNED_INT, out.ctypes.data)
        else:
            self.gl.GetTexImage(self.target, 0, GL_RGBA, GL_FLOAT, out.ctypes.data)

    def delete(self):
        self.gl.DeleteTextures(1, C.byref(_u(self.id)))


def _rgba(a):
    """Un array (alto, ancho, 1 o 3) como RGBA float32, para subirlo."""
    h, w, c = a.shape
    out = np.zeros((h, w, 4), np.float32)
    out[..., :c] = a
    return out


def _gauss_weights(sigma, radius):
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    out = np.zeros(64, np.float32)
    out[:k.size] = k / k.sum()
    return out


class GpuLook(fx.Look):
    """look.Look hecho en la gráfica. Necesita un contexto OpenGL actual."""

    def __init__(self, gl, settings=None):
        super().__init__(settings)
        self.gl = gl
        self.programs = {name: _Program(gl, name, body) for name, body in SHADERS.items()}
        self.textures = {}
        self.static = {}            # placas de grano, curvas y LUT ya subidas, por su clave
        gl.PixelStorei(GL_PACK_ALIGNMENT, 4)
        gl.PixelStorei(GL_UNPACK_ALIGNMENT, 4)

    # -- texturas

    def tex(self, name, w, h, fmt=GL_RGBA32F):
        """Una textura de trabajo con ese nombre y tamaño (se reutiliza entre fotogramas)."""
        t = self.textures.get(name)
        if t is None or t.size != (w, h):
            if t is not None:
                t.delete()
            t = self.textures[name] = _Texture(self.gl, w, h, fmt=fmt)
        return t

    def static_tex(self, key, make):
        """Una textura que no cambia de un fotograma a otro (placa de grano, curvas, LUT)."""
        t = self.static.get(key)
        if t is None:
            data = make()
            if len(self.static) > 12:           # se va la más antigua (las de relleno, nunca)
                old = next(k for k in self.static if k[0] not in ("dummy2", "dummy3"))
                self.static.pop(old).delete()
            if data.ndim == 4:
                t = _Texture(self.gl, data.shape[2], data.shape[1], data.shape[0])
            else:
                t = _Texture(self.gl, data.shape[1], data.shape[0])
            t.upload(data)
            self.static[key] = t
        return t

    # -- pasadas

    def run(self, name, out, images=(), samplers=(), **uniforms):
        """Una pasada: `out` es lo que se escribe (y da el tamaño); `images`, las texturas que
        se leen y escriben píxel a píxel (unidad 0, 1...); `samplers`, las que se leen."""
        gl = self.gl
        prog = self.programs[name]
        gl.UseProgram(prog.id)
        for unit, (tex, access) in enumerate(images):
            gl.BindImageTexture(unit, tex.id, 0, 0, 0, access, tex.fmt)
        for unit, tex in enumerate(samplers):
            gl.ActiveTexture(GL_TEXTURE0 + unit)
            gl.BindTexture(tex.target, tex.id)
        prog.set("size", out.size)
        for k, v in uniforms.items():
            prog.set(k, v)
        gl.DispatchCompute((out.w + 15) // 16, (out.h + 15) // 16, 1)
        gl.MemoryBarrier(GL_ALL_BARRIER_BITS)
        gl.Flush()          # que la gráfica empiece ya: ninguna pasada se queda esperando a otra

    def apply(self, rgba, frame=0):
        """Como Look.apply: float32 lineal, y el resultado en el mismo array."""
        h, w = rgba.shape[:2]
        cur = self.tex("a", w, h)
        cur.upload(rgba)
        self._effects(cur, frame).download(rgba)
        return rgba

    def develop(self, src, frame, out):
        """Para el visor: src tal como viene del fichero (8 o 16 bits sRGB, o half o float
        lineal) y el resultado en `out`, float16, que es lo que pinta. Subir 8 bits es la
        cuarta parte que subir float32, y bajar half la mitad."""
        h, w = src.shape[:2]
        fmt = SOURCE_FORMATS[src.dtype.name][0]
        raw = self.tex("src_" + src.dtype.name, w, h, fmt)
        raw.upload(src)
        cur = self.tex("a", w, h)
        self.run("source", cur, [(cur, GL_WRITE_ONLY)], [raw], srgb=int(src.dtype.kind == "u"))
        cur = self._effects(cur, frame)
        half = self.tex("half", w, h, GL_RG32UI)
        self.run("to_half", half, [(half, GL_WRITE_ONLY)], [cur])
        half.download(out)
        return out

    def _effects(self, cur, frame):
        """Todos los efectos sobre la textura; devuelve la que tiene el resultado."""
        s = self.settings
        dist, ab = s["distortion"], s["aberration"]
        tv = dist["amount"] if dist["on"] else 0.0
        ca = ab["amount"] if ab["on"] else 0.0
        if tv:
            cur = self._distort(cur, tv, dist["fill"])
        if ca:
            cur = self._aberrate(cur, ca, dist["fill"])
        gl_ = s["glow"]
        if gl_["on"] and (gl_["amount"] or gl_["only"]):
            self._glow(cur, gl_)
        v = s["vignette"]
        if v["on"] and v["amount"]:
            self._vignette(cur, v)
        hal = s["halation"]
        if hal["on"] and hal["amount"]:
            self._halation(cur, hal, s["grain"]["gauge"] if s["grain"]["kind"] == "film" else "35")
        g = s["grain"]
        if g["on"] and g["amount"]:
            if g["kind"] == "film":
                self._film_grain(cur, g, frame)
            else:
                self._sensor_noise(cur, g, frame)
        if s["grade"]["on"]:
            self._grade(cur, s["grade"])
        hl = s["highlights"]
        if hl["on"]:
            knee, _, a = fx.shoulder_params(hl["knee"], hl["white"])
            self.run("highlights", cur, [(cur, GL_READ_WRITE)], knee=knee, a=a)
        self._display(cur, s)
        return cur

    def _distort(self, src, tv, fill):
        w, h = src.size
        half = math.hypot(w, h) / 2
        lam = fx.distortion_lambda(tv, w, h)
        zoom = 1.0
        if fill:
            # lo mismo que _Warp: acercar lo justo para que ninguna muestra caiga fuera,
            # mirando solo el borde de la imagen
            xs = (np.arange(w, dtype=np.float32) + 0.5 - w / 2) / half
            ys = (np.arange(h, dtype=np.float32) + 0.5 - h / 2) / half
            X = np.concatenate([xs, xs, np.full(h, xs[0]), np.full(h, xs[-1])])
            Y = np.concatenate([np.full(w, ys[0]), np.full(w, ys[-1]), ys, ys])
            s = (1 / (1 + lam * (X * X + Y * Y))).astype(np.float32)
            zoom = max(1.0, float(np.abs(X * s * half).max() / (w / 2)), float(np.abs(Y * s * half).max() / (h / 2)))
        out = self.tex("b" if src is self.textures.get("a") else "a", w, h)
        self.run("distort", out, [(out, GL_WRITE_ONLY)], [src], half_diag=half, lam=lam, zoom=zoom)
        return out

    def _aberrate(self, src, ca, fill):
        w, h = src.size
        half = math.hypot(w, h) / 2
        ca_px = abs(ca) / 100 * half
        steps = int(min(9, max(3, 2 * math.ceil(ca_px / 2) + 1)))
        t = np.linspace(0, 1, steps)
        mags = 1 + (t - 0.5) * ca / 100
        if fill:
            mags = mags / mags.min()
        hat = lambda c: np.clip(1 - 2 * np.abs(t - c), 0, 1)
        weights = np.stack([hat(1.0), hat(0.5), hat(0.0)], axis=1)
        weights /= weights.sum(axis=0)
        m = np.ones(9, np.float32)
        m[:steps] = mags
        wt = np.zeros((9, 3), np.float32)
        wt[:steps] = weights
        out = self.tex("b" if src is self.textures.get("a") else "a", w, h)
        self.run("aberrate", out, [(out, GL_WRITE_ONLY)], [src], steps=steps, mags=m, wts=wt)
        return out

    def _glow(self, img, g):
        w, h = img.size
        t = 0.18 * 2.0 ** g["threshold"]
        k = t * g["knee"] + 1e-5
        half = self.tex("glow_src", (w + 1) // 2, (h + 1) // 2)
        self.run("karis", half, [(half, GL_WRITE_ONLY)], [img], src_size=img.size, t=t, k=k)
        # la pirámide: cada nivel, el anterior a la mitad y desenfocado [1 2 1]
        levels, src = [], half
        for i in range(fx.glow_levels(w, h, g["size"])):
            if i:
                if min(levels[-1].h, levels[-1].w) < 4:
                    break
                lw, lh = (src.w + 1) // 2, (src.h + 1) // 2
            else:
                lw, lh = src.w, src.h
            tmp = self.tex(f"glow_tmp{i}", lw, lh)
            lev = self.tex(f"glow_lev{i}", lw, lh)
            self.run("blur121", tmp, [(tmp, GL_WRITE_ONLY)], [src], src_size=src.size, dir=(1, 0), down=int(i > 0))
            self.run("blur121", lev, [(lev, GL_WRITE_ONLY)], [tmp], src_size=tmp.size, dir=(0, 1), down=0)
            levels.append(lev)
            src = lev
        weights = np.array([2.0 ** (i * (2 - g["falloff"])) for i in range(len(levels))], np.float32)
        weights /= weights.sum()
        acc, scale = levels[-1], float(weights[-1])
        sat = g["saturation"]
        if len(levels) == 1:
            out = self.tex("glow_acc0", acc.w, acc.h)
            self.run("up_add", out, [(out, GL_WRITE_ONLY)], [acc, acc], src_size=acc.size, weight=scale,
                     src_scale=0.0, saturate=int(sat != 1), saturation=float(sat))
            acc = out
        for i in range(len(levels) - 2, -1, -1):
            out = self.tex(f"glow_acc{i}", levels[i].w, levels[i].h)
            self.run("up_add", out, [(out, GL_WRITE_ONLY)], [acc, levels[i]], src_size=acc.size,
                     weight=float(weights[i]), src_scale=scale, saturate=int(i == 0 and sat != 1),
                     saturation=float(sat))
            acc, scale = out, 1.0
        self.run("glow_add", img, [(img, GL_READ_WRITE)], [acc], src_size=acc.size,
                 amount=float(g["amount"]), only=int(bool(g["only"])))

    def _vignette(self, img, v):
        w, h = img.size
        half = math.hypot(w, h) / 2
        sx = half + (w / 2 - half) * v["fit"]
        sy = half + (h / 2 - half) * v["fit"]
        n = 2.0 * 4.0 ** v["roundness"]
        corner = ((w / 2 / sx) ** n + (h / 2 / sy) ** n) ** (1 / n)
        self.run("vignette", img, [(img, GL_READ_WRITE)], scale=(float(sx), float(sy)), n=n, corner=corner,
                 midpoint=float(v["midpoint"]), amount=float(v["amount"]))

    def _blur_wide(self, src, sigma_px, mode=0, threshold=0.0, prefix="wide"):
        """El desenfoque de _blur_wide: reducir d veces, gaussiana y (al usarlo) ampliar.
        Devuelve (textura reducida, d)."""
        w, h = src.size
        d = max(1, int(sigma_px / 3))
        hs, ws = max(1, h // d), max(1, w // d)
        small = self.tex(prefix + "0", ws, hs)
        tmp = self.tex(prefix + "1", ws, hs)
        self.run("box_down", small, [(small, GL_WRITE_ONLY)], [src], d=d, mode=mode, threshold=threshold)
        s = sigma_px / d
        radius = min(31, int(math.ceil(4 * s)))
        k = _gauss_weights(s, radius)
        self.run("gauss", tmp, [(tmp, GL_WRITE_ONLY)], [small], dir=(1, 0), radius=radius, k=k)
        self.run("gauss", small, [(small, GL_WRITE_ONLY)], [tmp], dir=(0, 1), radius=radius, k=k)
        return small, d

    def _halation(self, img, hal, gauge):
        w = img.w
        threshold = 0.18 * 2.0 ** hal["threshold"]
        glow, d = self._blur_wide(img, fx.halation_radius_px(hal["radius"], gauge, w), 1, threshold, "hal")
        tint = tuple(float(c) * hal["amount"] for c in fx.HALATION_TINT)
        self.run("halation_add", img, [(img, GL_READ_WRITE)], [glow], src_size=glow.size, d=d, tint=tint)

    def _film_grain(self, img, g, frame):
        w, h = img.size
        stops, size = fx.film_grain_params(g["stock"], g["gauge"], w)
        key = ("plate", w, h, round(size, 3))
        plate = self.static_tex(key, lambda: _rgba(fx._gaussian_noise(h, w, 3, size, 7)))
        mix = fx.grain_mix(float(np.clip(g["color"], 0, 1)))
        dy, dx = fx.frame_offset(frame, h, w)
        self.run("film_grain", img, [(img, GL_READ_WRITE)], [plate], offset=(dx, dy), mixm=mix,
                 scale=tuple(float(x) for x in stops * float(g["amount"])),
                 toe=0.18 * 2.0 ** -fx.TOE_STOPS, max_boost=fx.TOE_MAX_BOOST)

    def _sensor_noise(self, img, g, frame):
        w, h = img.size
        nr = float(np.clip(g.get("nr", 0.0), 0, 1))
        chroma_size = 0.5 + 3.0 * nr
        lum = self.static_tex(("lum", w, h), lambda: _rgba(fx._gaussian_noise(h, w, 1, 0.5, 11)))
        chroma = self.static_tex(("chroma", w, h, chroma_size),
                                 lambda: _rgba(fx._gaussian_noise(h, w, 3, chroma_size, 13)))
        k = float(np.clip(g["color"], 0, 1))
        a, b = fx.digital_noise_params(g["iso"], w, h)
        amount = float(g["amount"]) ** 2
        dy, dx = fx.frame_offset(frame, h, w)
        self.run("sensor_noise", img, [(img, GL_READ_WRITE)], [lum, chroma], offset=(dx, dy),
                 wl=math.sqrt(1 - k) * (1 - 0.4 * nr), wc=math.sqrt(k) * (1 - 0.3 * nr),
                 a=a * amount, b=b * amount, gain=tuple(fx.SENSOR_CHANNEL_GAIN))

    def _grade(self, img, gr):
        wb = fx.white_balance_matrix(gr["temperature"], gr["tint"])
        exposure, contrast, gamma, sat = gr["exposure"], gr["contrast"], gr["gamma"], gr["saturation"]
        if wb is None and not exposure and contrast == 1 and gamma == 1 and sat == 1:
            return
        self.run("grade", img, [(img, GL_READ_WRITE)], use_wb=int(wb is not None),
                 wb=wb if wb is not None else np.eye(3, dtype=np.float32), use_gain=int(bool(exposure)),
                 gain=2.0 ** exposure, use_contrast=int(contrast != 1), contrast=float(contrast),
                 use_gamma=int(gamma != 1), inv_gamma=1 / gamma, use_sat=int(sat != 1), saturation=float(sat))

    def _display(self, img, s):
        bal, cv, lt, sh = s["balance"], s["curves"], s["lut"], s["sharpen"]
        use_balance = bal["on"] and any(any(bal[z]) for z in fx.BALANCE_ZONES)
        use_curves = cv["on"] and not all(fx.is_identity(cv[c]) for c in fx.CURVE_CHANNELS)
        lut = self.lut(lt["path"]) if lt["on"] and lt["path"] and lt["mix"] else None
        use_sharpen = sh["on"] and sh["amount"]
        if not (use_balance or use_curves or lut is not None or use_sharpen):
            return
        uniforms = dict(use_balance=int(bool(use_balance)), use_curves=int(bool(use_curves)), lut_kind=0,
                        to_enc=int(bool(use_sharpen)))
        dummy = self.static_tex(("dummy2",), lambda: np.zeros((1, 2, 4), np.float32))
        dummy3 = self.static_tex(("dummy3",), lambda: np.zeros((2, 2, 2, 4), np.float32))
        samplers = [dummy, dummy, dummy3]
        if use_balance:
            o = fx.balance_offsets(bal)
            uniforms.update(shadows=tuple(map(float, o[0])), midtones=tuple(map(float, o[1])),
                            lights=tuple(map(float, o[2])))
        if use_curves:
            key = ("curves", repr([cv[c] for c in fx.CURVE_CHANNELS]))
            luts, ends = self._cached(key, lambda: fx.curves_table(cv))
            samplers[0] = self.static_tex(key, lambda: _rgba(luts.T[None]))
            uniforms.update(curve_n=luts.shape[1] - 1, end_y=tuple(float(e[0]) for e in ends),
                            end_slope=tuple(float(e[1]) for e in ends))
        if lut is not None:
            table, lo, hi = lut
            key = ("lut", lt["path"], id(table))
            if table.ndim == 2:
                samplers[1] = self.static_tex(key, lambda: _rgba(table[None]))
            else:
                samplers[2] = self.static_tex(key, lambda: np.concatenate(
                    [table, np.zeros(table.shape[:3] + (1,), np.float32)], axis=3))
            uniforms.update(lut_kind=1 if table.ndim == 2 else 2, lut_n=table.shape[0],
                            lut_lo=tuple(map(float, lo)), lut_hi=tuple(map(float, hi)), lut_mix=float(lt["mix"]))
        enc = self.tex("enc", img.w, img.h) if use_sharpen else img
        self.run("display", img, [(img, GL_READ_WRITE), (enc, GL_WRITE_ONLY)], samplers, **uniforms)
        if use_sharpen:
            radius = max(sh["radius"], 0.3)
            if radius < 3:
                # el desenfoque pequeño de _blur_small, a tamaño completo
                r = int(math.ceil(3 * radius))
                k = _gauss_weights(radius, r)
                tmp = self.tex("sharp_tmp", img.w, img.h)
                blurred = self.tex("sharp_blur", img.w, img.h)
                self.run("gauss", tmp, [(tmp, GL_WRITE_ONLY)], [enc], dir=(1, 0), radius=r, k=k)
                self.run("gauss", blurred, [(blurred, GL_WRITE_ONLY)], [tmp], dir=(0, 1), radius=r, k=k)
                d = 1
            else:
                blurred, d = self._blur_wide(enc, radius, prefix="sharp")
            self.run("sharpen", img, [(img, GL_READ_WRITE)], [enc, blurred], src_size=blurred.size, d=d,
                     amount=float(sh["amount"]))


# ------------------------------------------------------------------ el proceso de la gráfica

def _worker_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (PACKAGE_ROOT, env.get("PYTHONPATH")) if p)
    env["EGL_LOG_LEVEL"] = "fatal"          # Mesa avisa al probar la NVIDIA, que no es suya
    for k in ("__NV_PRIME_RENDER_OFFLOAD", "__GLX_VENDOR_LIBRARY_NAME", "__EGL_VENDOR_LIBRARY_FILENAMES",
              "DRI_PRIME"):
        env.pop(k, None)
    return env


def _pci_id(node):
    """vendor:device de la tarjeta de ese nodo (10de:249d), sin abrirla."""
    base = Path(f"/sys/class/drm/{Path(node).name}/device")
    try:
        return ":".join((base / f).read_text().strip().removeprefix("0x") for f in ("vendor", "device"))
    except OSError:
        return "?"


def _benchmark(gl):
    """Milisegundos de un desenfoque gaussiano de 33 tomas, en las dos direcciones, sobre una
    imagen de 1080p: lo que más se parece a los efectos (leer mucho y hacer cuentas). El
    mejor de varios intentos, después de calentar (la gráfica sube de reloj al trabajar)."""
    prog = _Program(gl, "gauss", SHADERS["gauss"])
    a, b = _Texture(gl, 1920, 1080), _Texture(gl, 1920, 1080)
    gl.UseProgram(prog.id)
    prog.set("size", (1920, 1080))
    prog.set("radius", 16)
    prog.set("k", _gauss_weights(5.0, 16))
    best = float("inf")
    for i in range(8):
        t = time.perf_counter()
        for src, dst, d in ((a, b, (1, 0)), (b, a, (0, 1))):
            gl.ActiveTexture(GL_TEXTURE0)
            gl.BindTexture(GL_TEXTURE_2D, src.id)
            gl.BindImageTexture(0, dst.id, 0, 0, 0, GL_WRITE_ONLY, GL_RGBA32F)
            prog.set("dir", d)
            gl.DispatchCompute(120, 68, 1)
            gl.MemoryBarrier(GL_ALL_BARRIER_BITS)
        gl.Finish()                         # de una en una: nunca una cola larga en la gráfica
        if i >= 3:
            best = min(best, (time.perf_counter() - t) * 1000)
    a.delete()
    b.delete()
    return best


def _ranking_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "seqview" / "gpus.json"


def ranked_devices(egl, refresh=False):
    """[{name, node, display, ms}] de las gráficas que abren OpenGL 4.3, de la más rápida a la
    más lenta. Medirlas despierta cada una (un segundo o dos), así que se guarda y solo se
    vuelve a medir si cambian las tarjetas (sus ids PCI y sus nodos)."""
    devs = egl.devices()
    key = sorted(f"{node}={_pci_id(node)}" for _, node in devs)
    path = _ranking_path()
    if not refresh:
        try:
            saved = json.loads(path.read_text())
            if saved["key"] == key:
                return saved["devices"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    out, seen = [], set()
    for dev, node in devs:
        if node in seen:
            continue
        dpy = egl.open(dev)
        if not dpy:
            continue
        try:
            gl = GL(egl)
            name = gl.GetString(GL_RENDERER).decode(errors="replace")
            ms = _benchmark(gl)
        except Exception:
            continue
        finally:
            egl.MakeCurrent(dpy, None, None, None)
            egl.Terminate(dpy)
        seen.add(node)
        out.append({"name": name, "node": node, "display": display_gpu(node), "ms": round(ms, 2)})
    out.sort(key=lambda d: d["ms"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, "devices": out}, indent=1))
        tmp.replace(path)
    except OSError:
        pass
    return out


def list_devices(refresh=False):
    return ranked_devices(EGL(), refresh)


def _open(egl, want):
    """El contexto de la gráfica pedida ({name, node}, o "auto": la más rápida): primero la
    que tiene ese nodo, por no despertar las demás; si no es esa (los nodos pueden cambiar de
    un arranque a otro), la que se llame así; si ya no está, la más rápida."""
    ranking = ranked_devices(egl)
    if not isinstance(want, dict):
        if not ranking:
            return None, None
        want = ranking[0]
    devs = sorted(egl.devices(), key=lambda d: d[1] != want.get("node"))
    for dev, node in devs:
        dpy = egl.open(dev)
        if not dpy:
            continue
        name = GL(egl).GetString(GL_RENDERER).decode(errors="replace")
        if name == want.get("name"):
            return dpy, name
        egl.MakeCurrent(dpy, None, None, None)
        egl.Terminate(dpy)
    if ranking and want is not ranking[0]:
        return _open(egl, ranking[0])
    return None, None


def serve(want):
    from multiprocessing import shared_memory
    pipe = sys.stdout
    egl = EGL()
    dpy, name = _open(egl, want)
    if not dpy:
        pipe.write(json.dumps({"error": "no se ha podido abrir la gráfica"}) + "\n")
        pipe.flush()
        return
    try:
        look = GpuLook(GL(egl))
    except Exception as e:
        pipe.write(json.dumps({"error": str(e)}) + "\n")
        pipe.flush()
        return
    pipe.write(json.dumps({"ready": True, "name": name}) + "\n")
    pipe.flush()
    shm = None
    while True:
        ready, _, _ = select.select([sys.stdin], [], [], IDLE_EXIT_S)
        if not ready:
            return                          # sin trabajo: que la gráfica pueda dormirse
        line = sys.stdin.readline()
        if not line:
            return
        try:
            req = json.loads(line)
            if shm is None or shm.name != req["shm"]:
                if shm is not None:
                    shm.close()
                shm = shared_memory.SharedMemory(req["shm"], track=False)
            h, w = req["h"], req["w"]
            src = np.ndarray((h, w, 4), np.dtype(req["dtype"]), buffer=shm.buf)
            look.set(req["settings"])
            if req["develop"]:
                # en half, encima de la entrada, que ya está en la gráfica
                look.develop(src, req["frame"], np.ndarray((h, w, 4), np.float16, buffer=shm.buf))
            else:
                look.apply(src, req["frame"])
            reply = {"ok": True}
        except Exception as e:
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        pipe.write(json.dumps(reply) + "\n")
        pipe.flush()


# ------------------------------------------------------------------ lo que usa seqview

class Engine:
    """Los efectos en la gráfica elegida, desde cualquier hilo (de uno en uno). apply()
    devuelve None si no hay gráfica o ha fallado: entonces se hacen con numpy."""

    worker = WORKER             # el proceso de la gráfica (las pruebas lo cambian por uno que falla)

    def __init__(self, choice="cpu"):
        self.choice = choice
        self.lock = threading.Lock()
        self.proc = None
        self.shm = None
        self.name = None            # la gráfica que se está usando
        self.error = None           # por qué no se usa ninguna
        self.on_error = None        # se llama (una vez, desde cualquier hilo) si la gráfica falla
        self._devices = None

    def set_choice(self, choice):
        """"cpu", "auto" o {name, node} de devices()."""
        with self.lock:
            if choice != self.choice:
                self._stop()
                self.choice = choice
                self.error = None

    def devices(self, refresh=False):
        if self._devices is None or refresh:
            try:
                r = subprocess.run([*self.worker, "--list"], capture_output=True, text=True,
                                   timeout=30, env=_worker_env())
                self._devices = json.loads(r.stdout or "[]")
            except (OSError, ValueError, subprocess.TimeoutExpired):
                self._devices = []
        return self._devices

    def _start(self):
        want = self.choice if isinstance(self.choice, dict) else "auto"
        self.proc = subprocess.Popen([*self.worker, "--serve", json.dumps(want)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, env=_worker_env())
        reply = self._read(REPLY_S)
        if not reply or not reply.get("ready"):
            raise RuntimeError((reply or {}).get("error", "la gráfica no responde"))
        self.name = reply["name"]

    def _read(self, timeout):
        ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not ready:
            return None
        line = self.proc.stdout.readline()
        return json.loads(line) if line else None

    def _stop(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc.stdout.close()
            self.proc = None
        self.name = None

    def close(self):
        with self.lock:
            self._stop()
            if self.shm is not None:
                self.shm.close()
                self.shm.unlink()
                self.shm = None

    def _buffer(self, nbytes):
        from multiprocessing import shared_memory
        if self.shm is None or self.shm.size < nbytes:
            if self.shm is not None:
                self.shm.close()
                self.shm.unlink()
            self.shm = shared_memory.SharedMemory(create=True, size=nbytes)
        return self.shm

    def apply(self, settings, rgba, frame=0):
        """Los efectos sobre rgba (float32, alto × ancho × 4, lineal): un array nuevo, o None
        si se han de hacer con numpy."""
        return self._call(settings, rgba, frame, False, lambda out: out.copy())

    def develop(self, settings, src, frame, consume):
        """Para el visor: src (alto × ancho × 4) tal como viene del fichero, uint8 o uint16 en
        sRGB o float16 o float32 lineal; consume(resultado float16) se llama con la gráfica
        aún reservada (sin copiar el resultado) y lo que devuelva es lo que devuelve esto. None
        si se han de hacer con numpy."""
        return self._call(settings, src, frame, True, consume)

    def _fail(self, error):
        self._stop()
        self.error = str(error) or type(error).__name__
        if self.on_error is not None:
            self.on_error(self.error)

    def _call(self, settings, src, frame, develop, consume):
        if self.choice == "cpu" or self.error:
            return None
        h, w = src.shape[:2]
        out_dtype = np.float16 if develop else np.float32
        with self.lock:
            for attempt in range(2):        # si se cerró por estar sin trabajo, se vuelve a abrir
                try:
                    if self.proc is None or self.proc.poll() is not None:
                        self._start()
                    shm = self._buffer(max(src.nbytes, h * w * 4 * np.dtype(out_dtype).itemsize))
                    np.copyto(np.ndarray(src.shape, src.dtype, buffer=shm.buf), src)
                    self.proc.stdin.write(json.dumps({
                        "settings": settings, "frame": int(frame), "h": h, "w": w, "shm": shm.name,
                        "dtype": src.dtype.name, "develop": develop}) + "\n")
                    self.proc.stdin.flush()
                    reply = self._read(REPLY_S)
                    if reply is None:
                        raise RuntimeError("la gráfica no ha contestado")
                    if not reply.get("ok"):
                        raise RuntimeError(reply.get("error", "error en la gráfica"))
                    return consume(np.ndarray((h, w, 4), out_dtype, buffer=shm.buf))
                except (BrokenPipeError, OSError) as e:
                    self._stop()
                    if attempt:
                        self._fail(e)
                except Exception as e:
                    self._fail(e)
                    return None
        return None


if __name__ == "__main__":
    if sys.argv[1:2] == ["--list"]:
        print(json.dumps(list_devices(refresh="--medir" in sys.argv)))
    elif sys.argv[1:2] == ["--serve"]:
        serve(json.loads(sys.argv[2]) if len(sys.argv) > 2 else "auto")
