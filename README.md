# Konvi

Conversor de vídeo para Linux, pensado para que sea lo más fácil posible: arrastras los
vídeos, eliges a qué los quieres pasar y pulsas **Convertir**. La idea es la de AVS
Video Converter (una fila de destinos y perfiles listos para usar), con lo que hace bien
HandBrake por dentro, y con el aspecto nativo de tu escritorio (Plasma, GNOME...).

Solo convierte: no recorta, no une vídeos ni añade subtítulos.

## Cómo se usa

1. **Arrastra** vídeos o carpetas enteras a la ventana (o «Añadir vídeos…», o clic derecho
   en Dolphin → «Convertir con Konvi»).
2. **Elige el destino** en la fila de arriba (MP4, MKV, WebM, Edición, MPEG/DVD, Web y
   redes, Móvil y TV, Solo audio) y el **perfil** en el desplegable. Pasa el ratón por
   encima de cada cosa: todo explica lo que hace.
3. **Convertir.** Se puede pausar, detener, y seguir añadiendo vídeos mientras convierte.

El perfil se aplica a todos los vídeos de la lista, o solo a los seleccionados si
seleccionas algunos: así se puede pasar uno a MP4 y otro a MP3 en la misma tanda.

**Ajustes…** permite cambiar la calidad (o pedir un tamaño final en MB), la resolución
máxima, los fps, la velocidad y el audio, y guardarlo en **Mis perfiles**.

Los vídeos se guardan junto al original o en la carpeta que elijas. **Nunca se sobrescribe
nada**: si el nombre existe, se añade `(2)`, `(3)`… Mientras se convierte, el fichero se
llama `nombre.konvi-tmp.mp4` y solo recibe su nombre al terminar bien: un vídeo a medias
nunca parece terminado.

## Perfiles

| Destino | Perfiles |
|---|---|
| **MP4** | H.264 (misma resolución, 1080p, 720p, 480p), H.265, AV1, H.264 sin pérdida |
| **MKV** | H.264, H.265 y AV1 con **todas** las pistas de audio y subtítulos; FFV1 sin pérdida |
| **WebM** | VP9, AV1 |
| **Edición** | DNxHR HQ y LB para DaVinci Resolve, ProRes 422 HQ y Proxy |
| **MPEG / DVD** | MPEG-2, DVD PAL |
| **Web y redes** | YouTube/Vimeo, WhatsApp/Telegram, Discord (< 10 MB), correo (< 25 MB) |
| **Móvil y TV** | Móvil 1080p y 720p, Smart TV hasta 4K (H.265) |
| **Solo audio** | MP3, M4A (AAC), Opus, FLAC, WAV |

Lo que hace por dentro, tomado de HandBrake:

- **Calidad constante** (CRF): cada vídeo usa los bits que necesita. Con «tamaño final»,
  calidad constante con tope de bitrate, calculado para que el fichero quepa siempre.
- **Nunca agranda**: «1080p» reduce si el original es mayor y lo deja como está si es menor.
  En vídeos verticales del móvil se refiere al lado corto.
- **Desentrelaza** solo si el original es entrelazado (DVD, cámaras antiguas).
- **El audio se copia tal cual** si ya está en el formato pedido; si no, se convierte.
- Conserva capítulos, metadatos, la rotación del móvil y los 10 bits en H.265/AV1.
- Con la **tarjeta gráfica** (NVIDIA NVENC o VAAPI de AMD/Intel) H.264 y H.265 van mucho
  más rápido; viene activada siempre que la haya (desmarcarla vale para esa sesión). Konvi comprueba al arrancar qué funciona de verdad (no basta con que ffmpeg
  lo liste) y, si la tarjeta falla a mitad, vuelve a convertir ese vídeo con el procesador.
- No deja que el equipo se suspenda mientras convierte.

## Qué hay que instalar en el sistema

Konvi **no trae ffmpeg ni Qt dentro**: usa los del sistema.

| Programa | Para qué |
|---|---|
| **ffmpeg** (con `ffprobe`) | toda la conversión. Tiene que traer `libx264`, `libx265`, `libsvtav1`, `libvpx`, `libopus` y `libmp3lame` |
| **Python 3.10 o más nuevo** | Konvi está escrito en Python |
| **PySide6** (Qt 6 para Python) | la ventana |
| Driver de NVIDIA, o `libva` con el driver de tu gráfica | solo para usar la tarjeta gráfica (opcional) |

```bash
# Arch / EndeavourOS / Manjaro (probado)
sudo pacman -S --needed ffmpeg python pyside6
#   tarjeta gráfica: NVIDIA ya va con el driver «nvidia»; AMD: libva-mesa-driver; Intel: intel-media-driver

# Fedora: el ffmpeg oficial ("ffmpeg-free") NO trae H.264 ni H.265.
# Hay que cambiarlo por el de RPM Fusion:
sudo dnf install https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
sudo dnf swap ffmpeg-free ffmpeg --allowerasing
sudo dnf install python3-pyside6

# Debian 13 / Ubuntu 24.10 o más nuevo
sudo apt install ffmpeg python3-pyside6.qtwidgets python3-pyside6.qtnetwork

# openSUSE Tumbleweed: como en Fedora, el ffmpeg con H.264/H.265 está en Packman
sudo zypper install opi && opi codecs
sudo zypper install python3-pyside6
```

Solo está probado en Arch; en las otras distros son los nombres de sus paquetes, sin
haberlo probado allí. Para comprobar que tu ffmpeg trae lo necesario:

```bash
ffmpeg -hide_banner -encoders | grep -E 'libx264|libx265|libsvtav1|libvpx-vp9|libopus|libmp3lame'
```

Si falta alguno, los perfiles que lo usan fallarán con un error que lo dice.

## Instalar

```bash
git clone git@github.com:zebus3d/konvi.git
cd konvi
./install.sh
```

No necesita permisos de administrador. Instala en tu usuario:

- el programa en `~/.local/share/konvi` y la orden `konvi` en `~/.local/bin`;
- Konvi en el menú de aplicaciones, con su icono;
- «Convertir con Konvi» en el clic derecho de Dolphin, para vídeos, audios y carpetas
  (cierra y vuelve a abrir Dolphin para que aparezca).

`./uninstall.sh` lo quita todo menos tus perfiles (`~/.config/konvi`).

Para probarlo sin instalar: `./run.sh [vídeos…]`.

Si abres Konvi cuando ya está abierto (por ejemplo, desde Dolphin), los vídeos se
añaden a la ventana que ya tenías.

## Dónde guarda cosas

| Qué | Dónde |
|---|---|
| Ajustes (último perfil, carpeta, tamaño de la ventana) | `~/.config/konvi/konvi.conf` |
| Tus perfiles | `~/.config/konvi/presets.json` |
| Qué tarjeta gráfica funciona (se repite si cambia ffmpeg o el kernel) | `~/.cache/konvi/encoders.json` |
| Errores internos | `~/.cache/konvi/konvi.log` |

## Cómo está hecho

```
konvi/core/   sin Qt, con pruebas: perfiles, lectura con ffprobe, construcción del
              comando de ffmpeg (command.py, una función pura), progreso, nombres,
              detección de la tarjeta gráfica y el estado de cada trabajo
konvi/ui/     Qt (PySide6) con el estilo del sistema: ventana, cola, «Ajustes…» y el
              ejecutor (un ffmpeg detrás de otro con QProcess; pausa con SIGSTOP)
```

Pruebas:

```bash
python3 -m unittest tests.test_core              # el motor, en milisegundos
python3 -m unittest tests.test_real_conversions  # cada perfil con ffmpeg de verdad (~1 min)
python3 -m unittest tests.test_ui                # la ventana, sin pantalla
```
