#!/usr/bin/env bash
# Instala Konvi para este usuario (no necesita permisos de administrador).
set -eu

here=$(cd "$(dirname "$0")" && pwd)
data="${XDG_DATA_HOME:-$HOME/.local/share}"
app_dir="$data/konvi"
bin="$HOME/.local/bin/konvi"

missing=()
command -v ffmpeg >/dev/null || missing+=("ffmpeg")
command -v ffprobe >/dev/null || missing+=("ffprobe (viene con ffmpeg)")
command -v python3 >/dev/null || missing+=("python3")
python3 -c "import PySide6" 2>/dev/null || missing+=("PySide6")
python3 -c "import numpy" 2>/dev/null || missing+=("numpy")
if [ ${#missing[@]} -gt 0 ]; then
    echo "Faltan: ${missing[*]}" >&2
    echo "Mira «Qué hay que instalar en el sistema» en el README." >&2
    exit 1
fi
if ! ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264; then
    echo "Aviso: tu ffmpeg no trae H.264 (libx264). En Fedora y openSUSE, el ffmpeg" >&2
    echo "oficial viene sin él: instala el de RPM Fusion o Packman (ver README)." >&2
fi

rm -rf "$app_dir"
mkdir -p "$app_dir" "$(dirname "$bin")" "$data/applications" "$data/kio/servicemenus" \
         "$data/icons/hicolor/scalable/apps"
cp -r "$here/konvi" "$app_dir/"
find "$app_dir" -name __pycache__ -prune -exec rm -rf {} +

cat > "$bin" <<WRAPPER
#!/bin/sh
PYTHONPATH="$app_dir\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m konvi "\$@"
WRAPPER
chmod 755 "$bin"

sed "s|@KONVI@|$bin|g" "$here/data/konvi.desktop" > "$data/applications/konvi.desktop"
sed "s|@KONVI@|$bin|g" "$here/data/konvi-servicemenu.desktop" > "$data/kio/servicemenus/konvi.desktop"
chmod 755 "$data/kio/servicemenus/konvi.desktop"     # Plasma 6 solo carga los ejecutables
cp "$here/data/konvi.svg" "$data/icons/hicolor/scalable/apps/konvi.svg"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$data/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$data/icons/hicolor" 2>/dev/null || true

echo "Konvi instalado. Ábrelo desde el menú de aplicaciones, con «konvi» en una terminal,"
echo "o con clic derecho en un vídeo → «Convertir con Konvi» (cierra y abre Dolphin antes)."
