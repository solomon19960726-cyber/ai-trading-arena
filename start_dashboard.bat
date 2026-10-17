@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" http://localhost:8888/
python live_server.py
