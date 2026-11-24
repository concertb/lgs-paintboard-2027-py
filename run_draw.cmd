@echo off
rem LGS Paintboard 2027 - full repaint, then switch to guard patrol automatically
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

where python >nul 2>&1
if %errorlevel%==0 (set "PY=python") else (set "PY=E:\PY\python.exe")

"%PY%" -u -m pb draw --size 120 --place 440,1 --tokens 64 --out out %*

echo.
echo [exit %ERRORLEVEL%] press any key to close...
pause >nul
