@echo off
cd /d %~dp0
uv run aerorib gui
if errorlevel 1 pause
