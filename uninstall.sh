#!/usr/bin/env bash
# Quita Konvi. Tus perfiles (~/.config/konvi) se quedan, por si vuelves a instalarlo.
set -eu
data="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -rfv "$data/konvi"
rm -fv "$HOME/.local/bin/konvi" "$data/applications/konvi.desktop" \
       "$data/kio/servicemenus/konvi.desktop" "$data/icons/hicolor/scalable/apps/konvi.svg"
echo "Tus perfiles siguen en ~/.config/konvi (bórralo a mano si no los quieres)."
