@echo off
REM Launches Slay the Spire 2 (via Steam) if it isn't already running, waits
REM for the mod's API to come up, then starts the bot. Stops any existing bot
REM first so only one ever runs against the game at a time.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sts2_bot\scripts\bot_control.ps1" -Action start
echo.
pause
