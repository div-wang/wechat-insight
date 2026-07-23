@echo off
setlocal
set "ROOT=%~dp0"

if defined WECHAT_INSIGHT_PYTHON (
    set "PYTHON=%WECHAT_INSIGHT_PYTHON%"
) else if exist "%ROOT%.venv\Scripts\python.exe" (
    set "PYTHON=%ROOT%.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON=py -3"
    ) else (
        set "PYTHON=python"
    )
)

%PYTHON% "%ROOT%wechat_insight_cli.py" %*
exit /b %errorlevel%
