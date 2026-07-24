@echo off
setlocal
set "ROOT=%~dp0"
"%ROOT%runtime\pythonw.exe" "%ROOT%app\scripts\windows_agent.py" --once
exit /b %errorlevel%
