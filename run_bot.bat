@echo off
rem Starts the bot and restarts it automatically if it crashes.
cd /d "%~dp0"

:start
".venv\Scripts\python.exe" main.py
set EXITCODE=%ERRORLEVEL%

if %EXITCODE%==0 goto stopped
if %EXITCODE%==2 goto config_problem

echo [%date% %time%] Bot crashed with code %EXITCODE%. Restarting in 10 seconds...
timeout /t 10 /nobreak >nul
goto start

:config_problem
echo.
echo Fix the problem shown above (token or .env settings), then run this file again.
pause
exit /b 2

:stopped
echo Bot stopped.
exit /b 0
