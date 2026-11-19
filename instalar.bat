@echo off
cd /d "%~dp0"
echo Criando ambiente Python do CCT Vision...
py -3 -m venv .venv || python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if not exist .env copy .env.exemplo .env
echo.
echo Pronto. Abra o arquivo .env, coloque sua chave da API e execute iniciar.bat
pause
