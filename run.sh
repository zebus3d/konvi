#!/bin/sh
# Arranca Konvi desde el repo, sin instalar.
cd "$(dirname "$0")" && exec python3 -m konvi "$@"
