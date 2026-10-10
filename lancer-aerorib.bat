@echo off
chcp 65001 >nul
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo erreur : uv est introuvable. Installez-le depuis https://docs.astral.sh/uv/ puis relancez.
  pause
  exit /b 1
)
uv run aerorib gui
if errorlevel 1 pause
