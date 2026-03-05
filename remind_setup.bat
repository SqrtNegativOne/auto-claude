@echo off
:: Claude Code Usage Notifier — First Run
:: Run this once to start the self-scheduling notification system.
:: After this, Windows Task Scheduler takes over automatically.

echo Checking requirements...

where uv >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: uv not found. Install from https://docs.astral.sh/uv/getting-started/installation/
    pause & exit /b 1
)

where npx >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: npx not found. Install Node.js from https://nodejs.org
    pause & exit /b 1
)

echo Running notifier and scheduling future runs...
uv run "%~dp0remind.py"

echo.
echo Done! Task Scheduler will now handle future notifications automatically.
echo To stop: schtasks /delete /tn "ClaudeUsageNotifier" /f
echo Log file: %USERPROFILE%\.claude\usage_notifier.log
pause