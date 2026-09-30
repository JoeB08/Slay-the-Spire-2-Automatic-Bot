@echo off
REM Stops the STS2 Silent bot.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bot_control.ps1" -Action stop
echo.
pause
