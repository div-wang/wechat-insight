@echo off
setlocal
set "ROOT=%~dp0"
"%ROOT%runtime\python.exe" "%ROOT%app\wechat_insight_cli.py" %*
exit /b %errorlevel%
