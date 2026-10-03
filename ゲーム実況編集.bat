@echo off
rem Double-click to start. You can also drop a folder onto this icon.
chcp 65001 >nul
call "%~dp0gamecut.bat" %*
echo.
pause
