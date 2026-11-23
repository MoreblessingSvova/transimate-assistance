@echo off
cd /d "%~dp0"
python -m transimate.server --port 7860
pause