@echo off
chcp 65001 > nul
cd /d "%~dp0"
py tool\server.py
if errorlevel 1 pause
