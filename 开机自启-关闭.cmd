@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pet-launcher.ps1" -Uninstall
ping -n 6 127.0.0.1 >nul
