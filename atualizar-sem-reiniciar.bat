@echo off
cd /d "%~dp0"
where git >nul 2>nul
if errorlevel 1 (
  echo Git nao encontrado. Instale o Git para Windows e clone o repositorio antes de usar este atalho.
  pause
  exit /b 1
)
echo Buscando atualizacoes do GitHub. O servidor ativo sera recarregado se o backend mudar...
git pull --ff-only origin main
if errorlevel 1 (
  echo A atualizacao nao foi aplicada. Confira a conexao ou salve suas alteracoes locais antes de tentar novamente.
  pause
  exit /b 1
)
echo Atualizacao concluida. Atualize a pagina do navegador com Ctrl+F5.
pause
