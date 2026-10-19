@echo off
cd /d "%~dp0"
echo Abra http://127.0.0.1:8000 no navegador.
py -3 server.py
pause
