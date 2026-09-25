@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pet-launcher.ps1" -Install
ping -n 7 127.0.0.1 >nul
