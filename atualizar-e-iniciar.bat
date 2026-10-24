@echo off
cd /d "%~dp0"
where git >nul 2>nul
if errorlevel 1 (
  echo Git nao encontrado. Instale o Git para Windows e clone o repositorio antes de usar este atalho.
  pause
  exit /b 1
)
echo Buscando atualizacoes do GitHub...
git pull --ff-only origin main
if errorlevel 1 (
  echo A atualizacao nao foi aplicada. Confira a conexao ou salve suas alteracoes locais antes de tentar novamente.
  pause
  exit /b 1
)
echo Iniciando Segura Gestao em http://127.0.0.1:8000
py -3 watch_server.py
pause
