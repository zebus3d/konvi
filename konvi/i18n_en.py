"""Traducción al inglés. Clave: el texto en español tal cual está en el código.

tests/test_i18n.py falla si falta alguna clave o si sobra alguna que ya no se usa.
"""

EN = {
    # ---------------------------------------------------------------- destinos
    "MP4": "MP4",
    "MP4: el formato que abre todo\n(móviles, teles, navegadores, Windows, Mac).":
        "MP4: the format that plays everywhere\n(phones, TVs, browsers, Windows, Mac).",
    "Web y redes": "Web & social",
    "YouTube, Vimeo, WhatsApp, Discord, correo...": "YouTube, Vimeo, WhatsApp, Discord, email...",
    "Móvil y TV": "Phone & TV",
    "Para verlo en el móvil, la tablet o la tele.": "To watch it on your phone, tablet or TV.",
    "Solo audio": "Audio only",
    "Saca solo el sonido: MP3, M4A, Opus, FLAC o WAV.": "Extracts just the sound: MP3, M4A, Opus, FLAC or WAV.",
    "Edición": "Editing",
    "Para editar en DaVinci Resolve, Premiere o Final Cut:\n"
    "códecs intermedios (DNxHR, ProRes) que van fluidos en la línea de tiempo.\n"
    "Ficheros muy grandes.":
        "For editing in DaVinci Resolve, Premiere or Final Cut:\n"
        "intermediate codecs (DNxHR, ProRes) that play smoothly on the timeline.\n"
        "Very large files.",
    "MKV": "MKV",
    "MKV (Matroska): guarda todas las pistas de audio y los subtítulos.\nIdeal para películas y series.":
        "MKV (Matroska): keeps every audio track and subtitle.\nGreat for films and TV series.",
    "WebM": "WebM",
    "WebM: vídeo para páginas web (VP9 o AV1, audio Opus).": "WebM: video for web pages (VP9 or AV1, Opus audio).",
    "MPEG / DVD": "MPEG / DVD",
    "MPEG-2: reproductores antiguos y DVD.": "MPEG-2: old players and DVD.",
    "Mis perfiles": "My profiles",
    "Los perfiles que has guardado desde «Personalizar…».": "The profiles you saved with “Customize…”.",

    # ---------------------------------------------------------------- perfiles
    "H.264 · misma resolución": "H.264 · same resolution",
    "El más compatible. Mantiene la resolución y la calidad del original.":
        "The most compatible. Keeps the original resolution and quality.",
    "H.264 · 1080p": "H.264 · 1080p",
    "Full HD. Si el original es más grande, se reduce; nunca se agranda.":
        "Full HD. If the original is bigger it is scaled down; it is never enlarged.",
    "H.264 · 720p": "H.264 · 720p",
    "HD: buen equilibrio entre calidad y tamaño.": "HD: a good balance between quality and size.",
    "H.264 · 480p (pequeño)": "H.264 · 480p (small)",
    "Calidad de DVD, ficheros pequeños.": "DVD quality, small files.",
    "H.265 (HEVC) · misma resolución": "H.265 (HEVC) · same resolution",
    "Más o menos la mitad de tamaño que H.264 con la misma calidad.\nLo abren los aparatos de los últimos años.":
        "About half the size of H.264 at the same quality.\nPlays on devices from recent years.",
    "AV1 · misma resolución": "AV1 · same resolution",
    "El que menos ocupa. Los aparatos de menos de unos 4 años lo abren;\nlos más viejos, no.":
        "The smallest files. Devices up to about 4 years old play it;\nolder ones don't.",
    "H.264 sin pérdida": "H.264 lossless",
    "Copia exacta, píxel a píxel. Ocupa mucho. Lo abren VLC, mpv y DaVinci,\n"
    "pero no todos los reproductores (es H.264 High 4:4:4).":
        "Exact, pixel-perfect copy. Large files. VLC, mpv and DaVinci play it,\n"
        "but not every player does (it is H.264 High 4:4:4).",
    "H.264 · todas las pistas": "H.264 · all tracks",
    "Conserva todos los idiomas y subtítulos.": "Keeps every language and subtitle.",
    "H.265 · todas las pistas": "H.265 · all tracks",
    "Para guardar películas y series: la mitad de tamaño que H.264,\ncon todos los idiomas y subtítulos.":
        "For keeping films and series: half the size of H.264,\nwith every language and subtitle.",
    "AV1 · todas las pistas": "AV1 · all tracks",
    "Lo que menos ocupa, con todos los idiomas y subtítulos.": "The smallest files, with every language and subtitle.",
    "Sin pérdida (FFV1)": "Lossless (FFV1)",
    "Copia exacta, píxel a píxel, para archivar. Ocupa muchísimo.":
        "Exact, pixel-perfect copy for archiving. Very large files.",
    "VP9": "VP9",
    "El vídeo de la web: lo abren todos los navegadores.": "The web's video format: every browser plays it.",
    "AV1": "AV1",
    "Más pequeño que VP9; navegadores actuales.": "Smaller than VP9; current browsers.",
    "DaVinci Resolve · DNxHR HQ": "DaVinci Resolve · DNxHR HQ",
    "Calidad de edición. DaVinci gratuito en Linux no abre H.264/H.265 ni AAC:\nesto sí. Ficheros muy grandes.":
        "Editing quality. The free DaVinci on Linux can't open H.264/H.265 or AAC:\nit can open this. Very large files.",
    "DaVinci Resolve · DNxHR LB (proxy)": "DaVinci Resolve · DNxHR LB (proxy)",
    "Calidad de proxy: ligero para editar en equipos modestos.": "Proxy quality: light enough to edit on modest computers.",
    "ProRes 422 HQ": "ProRes 422 HQ",
    "El intermedio de Apple (Final Cut, Premiere, DaVinci).": "Apple's intermediate codec (Final Cut, Premiere, DaVinci).",
    "ProRes Proxy": "ProRes Proxy",
    "ProRes ligero para editar con proxies.": "Lightweight ProRes for proxy editing.",
    "MPEG-2 · misma resolución": "MPEG-2 · same resolution",
    "Para reproductores y programas antiguos.": "For old players and programs.",
    "DVD (PAL, 720×576)": "DVD (PAL, 720×576)",
    "MPEG-2 listo para grabar en un DVD (PAL, 25 fps).": "MPEG-2 ready to burn to a DVD (PAL, 25 fps).",
    "YouTube / Vimeo": "YouTube / Vimeo",
    "Lo que recomiendan YouTube y Vimeo: H.264 High, fotograma clave cada\nmedio segundo y audio AAC a 320 kbps.":
        "What YouTube and Vimeo recommend: H.264 High, a keyframe every\nhalf second and AAC audio at 320 kbps.",
    "WhatsApp / Telegram": "WhatsApp / Telegram",
    "720p y compresión alta: se envía rápido y se ve bien en el móvil.":
        "720p and high compression: quick to send and looks good on a phone.",
    "Discord (menos de 10 MB)": "Discord (under 10 MB)",
    "Ajusta la calidad para que el vídeo quepa en 10 MB.\nCuanto más largo el vídeo, peor se verá.":
        "Adjusts the quality so the video fits in 10 MB.\nThe longer the video, the worse it will look.",
    "Correo (menos de 25 MB)": "Email (under 25 MB)",
    "Ajusta la calidad para que el vídeo quepa como adjunto de 25 MB.":
        "Adjusts the quality so the video fits as a 25 MB attachment.",
    "Móvil o tablet · 1080p": "Phone or tablet · 1080p",
    "Lo abre cualquier móvil o tablet, también los antiguos.": "Plays on any phone or tablet, old ones too.",
    "Móvil · 720p (ocupa poco)": "Phone · 720p (small)",
    "Para llevar muchos vídeos en el móvil.": "To carry lots of videos on your phone.",
    "Smart TV · hasta 4K (H.265)": "Smart TV · up to 4K (H.265)",
    "Para la tele: H.265 hasta 4K, con el sonido envolvente si lo tiene.":
        "For the TV: H.265 up to 4K, with surround sound if it has it.",
    "MP3 · 320 kbps": "MP3 · 320 kbps",
    "El más compatible.": "The most compatible.",
    "M4A (AAC) · 256 kbps": "M4A (AAC) · 256 kbps",
    "Más calidad que MP3 al mismo tamaño.": "Better quality than MP3 at the same size.",
    "Opus · 160 kbps": "Opus · 160 kbps",
    "El que mejor suena por tamaño.": "The best sound for its size.",
    "FLAC (sin pérdida)": "FLAC (lossless)",
    "Sonido exacto, comprimido sin pérdida.": "Exact sound, losslessly compressed.",
    "WAV (sin comprimir)": "WAV (uncompressed)",
    "Para editar audio. Ocupa mucho.": "For audio editing. Large files.",

    # ---------------------------------------------------------------- calidad
    "Máxima": "Maximum",
    "Muy alta": "Very high",
    "Alta": "High",
    "Media": "Medium",
    "Baja": "Low",

    # ---------------------------------------------------------------- ventana principal
    "Perfil:": "Profile:",
    "El perfil decide cómo se convierte: formato, calidad, tamaño y sonido.\n"
    "Se aplica a todos los vídeos de la lista, o solo a los que selecciones.":
        "The profile decides how videos are converted: format, quality, size and sound.\n"
        "It applies to every video in the list, or only to the ones you select.",
    "Elige cómo convertir. Al pasar el ratón por cada opción\nde la lista verás para qué sirve.":
        "Choose how to convert. Hover over each option\nin the list to see what it is for.",
    "Personalizar…": "Customize…",
    "Cambiar la calidad, el tamaño final, la resolución o el sonido\n"
    "de este perfil y guardarlo en «Mis perfiles» para la próxima vez.":
        "Change this profile's quality, final size, resolution or sound\n"
        "and save it in “My profiles” for next time.",
    "Borrar este perfil de «Mis perfiles»": "Delete this profile from “My profiles”",
    "Borrar perfil": "Delete profile",
    "Para qué sirve el perfil elegido.": "What the chosen profile is for.",
    "Más rápido con la tarjeta gráfica": "Faster with the graphics card",
    "Comprobando qué puede hacer la tarjeta gráfica…": "Checking what the graphics card can do…",
    "Añadir vídeos…": "Add videos…",
    "Elegir vídeos o audios para convertir (Ctrl+O).\n"
    "También puedes arrastrarlos a la ventana, o una carpeta entera.":
        "Choose videos or audio files to convert (Ctrl+O).\n"
        "You can also drag them onto the window, or a whole folder.",
    "Quitar": "Remove",
    "Suelta aquí vídeos, audios o carpetas enteras.": "Drop videos, audio files or whole folders here.",
    "Arrastra aquí tus vídeos": "Drag your videos here",
    "También carpetas enteras, o pulsa «Añadir vídeos…»": "Whole folders too, or click “Add videos…”",
    "De una carpeta se añaden todos los vídeos y audios que haya dentro,\ntambién los de sus subcarpetas.":
        "From a folder, every video and audio file inside is added,\nincluding those in its subfolders.",
    "Vídeo": "Video",
    "Perfil": "Profile",
    "Estado": "Status",
    "El vídeo original y sus datos: tamaño de imagen, fotogramas\npor segundo, formato, sonido y duración.":
        "The original video and its details: picture size, frames\nper second, format, sound and length.",
    "Cómo se va a convertir cada vídeo. Para cambiarlo, selecciona\nel vídeo y elige otro perfil arriba.":
        "How each video will be converted. To change it, select\nthe video and choose another profile above.",
    "Cómo va cada vídeo. Clic derecho para más opciones.": "How each video is doing. Right-click for more options.",
    "Guardar en:": "Save to:",
    "La carpeta donde se guardan los vídeos convertidos.": "The folder where converted videos are saved.",
    "Dónde se guardan los vídeos convertidos.\nNunca se sobrescribe nada: si el nombre existe, se añade (2), (3)…":
        "Where converted videos are saved.\nNothing is ever overwritten: if the name exists, (2), (3)… is added.",
    "Abrir carpeta": "Open folder",
    "Abrir la carpeta con los vídeos que se acaban de convertir.": "Open the folder with the videos you just converted.",
    "Resumen de la lista: cuántos vídeos hay y cuánto duran en total,\no cómo va la conversión.":
        "List summary: how many videos there are and their total length,\nor how the conversion is going.",
    "Progreso de todos los vídeos de esta tanda.": "Progress of every video in this batch.",
    "Pausa": "Pause",
    "Pausar la conversión (se puede seguir después)": "Pause the conversion (you can resume it later)",
    "Detener": "Stop",
    "Parar la conversión. El vídeo que se estaba convirtiendo\nse descarta; los ya terminados se quedan.":
        "Stop the conversion. The video being converted\nis discarded; finished ones are kept.",
    "Convertir": "Convert",
    "¿Borrar el perfil «{name}» de «Mis perfiles»?\n\nLos vídeos de la lista que lo usan no cambian.":
        "Delete the profile “{name}” from “My profiles”?\n\nVideos in the list that use it won't change.",
    "Cancelar": "Cancel",
    "No se pudieron guardar tus perfiles:\n{error}": "Your profiles could not be saved:\n{error}",
    "Convierte con la tarjeta gráfica ({kind}): mucho más rápido.\n"
    "A la misma calidad, el fichero sale algo más grande.\n"
    "Solo acelera H.264 y H.265; el resto se hace con el procesador.":
        "Converts with the graphics card ({kind}): much faster.\n"
        "At the same quality, the file comes out somewhat larger.\n"
        "It only speeds up H.264 and H.265; everything else uses the processor.",
    "No se ha encontrado una tarjeta gráfica que ffmpeg pueda usar\n"
    "para convertir (NVIDIA NVENC o VAAPI). Se usa el procesador.":
        "No graphics card that ffmpeg can use for converting was found\n"
        "(NVIDIA NVENC or VAAPI). The processor is used instead.",
    "Junto al original": "Next to the original",
    "Elegir carpeta…": "Choose folder…",
    "Guardar los vídeos en": "Save the videos to",
    "Leyendo…": "Reading…",
    "Se guardará como:\n{path}": "It will be saved as:\n{path}",
    "En cola": "Queued",
    "Listo": "Ready",
    "quedan {time}": "{time} left",
    "Convirtiendo con la tarjeta gráfica": "Converting with the graphics card",
    "Convirtiendo": "Converting",
    "En pausa · {percent}": "Paused · {percent}",
    "Guardado en:\n{path}\n\nDoble clic para verlo.": "Saved to:\n{path}\n\nDouble-click to watch it.",
    "Hecho · {size}": "Done · {size}",
    "Error: {message}": "Error: {message}",
    "Cancelado": "Cancelled",
    "Se detuvo antes de terminar; no se ha guardado nada.": "It was stopped before finishing; nothing was saved.",
    "Añadir vídeos": "Add videos",
    "Vídeo y audio ({exts});;Todos los ficheros (*)": "Video and audio ({exts});;All files (*)",
    "Ver el vídeo convertido": "Watch the converted video",
    "Mostrar en la carpeta": "Show in folder",
    "Ver el original": "Watch the original",
    "Volver a convertir": "Convert again",
    "Ver el error completo": "Show the full error",
    "Quitar de la lista": "Remove from list",
    "Quitar los terminados": "Remove finished",
    "Error al convertir": "Conversion error",
    "No se pudo convertir «{name}».\n\n{message}": "“{name}” could not be converted.\n\n{message}",
    "{done} convertidos, {failed} con errores": "{done} converted, {failed} with errors",
    "1 vídeo convertido": "1 video converted",
    "{n} vídeos convertidos": "{n} videos converted",
    "1 vídeo": "1 video",
    "{n} vídeos": "{n} videos",
    "Convertir {videos} (Ctrl+Intro)": "Convert {videos} (Ctrl+Enter)",
    "Espera: aún se están leyendo los ficheros.": "Wait: the files are still being read.",
    "No hay nada listo para convertir.\n"
    "Los terminados se pueden repetir con clic derecho → «Volver a convertir».":
        "There is nothing ready to convert.\n"
        "Finished ones can be redone with right-click → “Convert again”.",
    "Añade vídeos para convertir.": "Add videos to convert.",
    "Continuar": "Resume",
    "Selecciona en la lista los vídeos que quieras quitar.": "Select the videos you want to remove in the list.",
    "Quitar de la lista los vídeos seleccionados (Supr).\nNo borra nada del disco.":
        "Remove the selected videos from the list (Delete).\nNothing is deleted from disk.",
    "Perfil del vídeo seleccionado:": "Profile for the selected video:",
    "Perfil de los {n} seleccionados:": "Profile for the {n} selected:",
    "Perfil para los {n} vídeos:": "Profile for the {n} videos:",
    "Perfil para el vídeo:": "Profile for the video:",
    "{index} de {total}": "{index} of {total}",
    "quedan unos {time}": "about {time} left",
    "En pausa · ": "Paused · ",
    "Convirtiendo ": "Converting ",
    "{videos} · {time} en total": "{videos} · {time} in total",
    "Hay una conversión en marcha. Si sales, el vídeo que se está convirtiendo se descarta.":
        "A conversion is in progress. If you quit, the video being converted is discarded.",
    "Detener y salir": "Stop and quit",
    "Seguir convirtiendo": "Keep converting",

    # ---------------------------------------------------------------- «Personalizar…»
    "Personalizar el perfil": "Customize the profile",
    "{name} (mío)": "{name} (mine)",
    "Con este nombre aparecerá en «Mis perfiles».": "It will appear under this name in “My profiles”.",
    "Nombre:": "Name:",
    "Calidad:": "Quality:",
    "Tamaño final:": "Final size:",
    "A la izquierda, ficheros más pequeños; a la derecha, más calidad.\n«Alta» se ve igual que el original en casi todo.":
        "To the left, smaller files; to the right, higher quality.\n“High” looks the same as the original in almost everything.",
    "Más pequeño": "Smaller",
    "Mejor calidad": "Better quality",
    "Hacia aquí: ficheros más pequeños, con menos calidad.": "This way: smaller files, lower quality.",
    "Hacia aquí: más calidad, ficheros más grandes.": "This way: higher quality, larger files.",
    "Calidad constante: cada vídeo ocupa lo que necesite para verse así.\nEs lo recomendado.":
        "Constant quality: each video takes the space it needs to look like this.\nThis is the recommended option.",
    "Elegir cuánto ocupará el fichero: la calidad se ajusta para caber.\n"
    "Útil para enviarlo por correo o a una web con límite de tamaño.":
        "Choose how big the file will be: the quality adjusts to fit.\n"
        "Useful for email or for websites with a size limit.",
    "La calidad elegida.": "The chosen quality.",
    "El vídeo se ajusta para no pasar de este tamaño (con el audio).\nCuanto más largo sea el vídeo, peor se verá.":
        "The video is adjusted so it does not exceed this size (audio included).\n"
        "The longer the video, the worse it will look.",
    "Es sin pérdida: la calidad es siempre la del original.": "It is lossless: the quality is always the original's.",
    "Este formato tiene calidad fija.": "This format has a fixed quality.",
    "Este formato no admite ajustar a un tamaño.": "This format can't be fitted to a size.",
    "Como el original": "Same as the original",
    "4K · 2160p": "4K · 2160p",
    "1440p": "1440p",
    "1080p (Full HD)": "1080p (Full HD)",
    "720p (HD)": "720p (HD)",
    "480p": "480p",
    "360p": "360p",
    "60": "60",
    "50": "50",
    "30": "30",
    "25": "25",
    "24": "24",
    "Rápida (el fichero sale algo más grande)": "Fast (the file comes out somewhat larger)",
    "Equilibrada": "Balanced",
    "Lenta (el fichero sale más pequeño)": "Slow (the file comes out smaller)",
    "Solo reduce: si el original es más pequeño, se deja como está.\nEn vídeos verticales se refiere al lado corto.":
        "Only scales down: if the original is smaller, it is left as is.\n"
        "For vertical videos it refers to the short side.",
    "Máximo de fotogramas por segundo. Si el original tiene menos,\nse deja como está.":
        "Maximum frames per second. If the original has fewer,\nit is left as is.",
    "Cuánto se esfuerza el compresor. La calidad es la misma;\nlo que cambia es el tamaño del fichero y lo que tarda.":
        "How hard the encoder works. The quality is the same;\nwhat changes is the file size and how long it takes.",
    "Resolución máxima:": "Maximum resolution:",
    "Fotogramas por segundo:": "Frames per second:",
    "Velocidad:": "Speed:",
    "El DVD tiene siempre 720×576 a 25 fps.": "DVD is always 720×576 at 25 fps.",
    "Calidad del sonido. 192 kbps está bien para casi todo;\n320 si es música.":
        "Sound quality. 192 kbps is fine for almost everything;\n320 for music.",
    "Pasar a estéreo": "Convert to stereo",
    "Mezcla el sonido envolvente (5.1, 7.1) a dos canales.\nRecomendado para móviles, auriculares y redes sociales.":
        "Mixes surround sound (5.1, 7.1) down to two channels.\nRecommended for phones, headphones and social media.",
    "Sonido:": "Sound:",
    "Guardar en Mis perfiles": "Save to My profiles",
    "Guarda estos ajustes como un perfil nuevo en «Mis perfiles»\ny lo aplica a los vídeos de la lista.":
        "Saves these settings as a new profile in “My profiles”\nand applies it to the videos in the list.",
    "Cerrar sin cambiar nada.": "Close without changing anything.",
    "Escribe un nombre": "Type a name",

    # ---------------------------------------------------------------- motor y avisos
    "Este fichero no tiene imagen: elige un perfil de «Solo audio».":
        "This file has no picture: choose an “Audio only” profile.",
    "Este fichero no tiene sonido.": "This file has no sound.",
    "No se sabe cuánto dura, así que no se puede ajustar a un tamaño.":
        "Its length is unknown, so it can't be fitted to a size.",
    "Es demasiado largo para caber en {mb} MB con una calidad mínima. Elige otro perfil.":
        "It is too long to fit in {mb} MB at a minimum quality. Choose another profile.",
    "Códec de vídeo desconocido: {codec}": "Unknown video codec: {codec}",
    "mono": "mono",
    "estéreo": "stereo",
    "{n} canales": "{n} channels",
    "{n} pistas de audio": "{n} audio tracks",
    "1 subtítulo": "1 subtitle",
    "{n} subtítulos": "{n} subtitles",
    "No se encuentra ffprobe: instala ffmpeg.": "ffprobe was not found: install ffmpeg.",
    "ffprobe tardó demasiado en leer el fichero.": "ffprobe took too long to read the file.",
    "No es un fichero de vídeo o audio que ffmpeg sepa leer.": "It is not a video or audio file that ffmpeg can read.",
    "No tiene ni vídeo ni audio.": "It has neither video nor audio.",
    "ffmpeg terminó con el código {code}.": "ffmpeg exited with code {code}.",
    "La tarjeta gráfica falló; se ha convertido sin ella.": "The graphics card failed; it was converted without it.",
    "No se pudo ejecutar ffmpeg. ¿Está instalado?": "ffmpeg could not be run. Is it installed?",
    "Convirtiendo vídeos": "Converting videos",
    "No se pudo leer: {error}": "It could not be read: {error}",
    "Algo ha fallado dentro de Konvi:\n{error}\n\nEl detalle está en {log}.":
        "Something went wrong inside Konvi:\n{error}\n\nThe details are in {log}.",
}
