#!/bin/bash
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
cd "$(dirname "$0")" || exit 1
if ! command -v uv >/dev/null 2>&1; then
  echo "erreur : uv est introuvable. Installez-le depuis https://docs.astral.sh/uv/ puis relancez."
  read -n1 -r -p "Appuyez sur une touche pour fermer..."
  exit 1
fi
uv run aerorib gui
