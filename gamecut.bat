@echo off
rem gamecut launcher (Windows)
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gamecut_setup.ps1" %*
exit /b %errorlevel%
