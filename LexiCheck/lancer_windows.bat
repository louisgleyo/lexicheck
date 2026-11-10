@echo off
cd /d "%~dp0"
if not exist ".env" (
 echo Copiez .env.example en .env et renseignez vos identifiants PISTE.
 pause
 exit /b 1
)
start "Ouvrir LexiCheck" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765"
python server.py
pause
