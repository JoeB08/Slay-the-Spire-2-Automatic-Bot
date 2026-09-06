@echo off
REM Shows whether the bot is running and whether the game's mod API is up.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sts2_bot\scripts\bot_control.ps1" -Action status
echo.
pause
