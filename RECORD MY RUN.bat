@echo off
REM Records YOUR run and what the bot would have done at each decision.
REM Read-only: it never sends an action to the game.
echo Stopping the bot first (two things driving one game fight each other)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sts2_bot\scripts\bot_control.ps1" -Action stop
echo.
echo Now play your run. Press Ctrl-C in this window when you are done.
echo.
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%~dp0sts2_bot\scripts\shadow_record.py"
pause
