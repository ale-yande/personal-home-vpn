@echo off
title Home VPN Server
echo ========================================================
echo        Starting Home VPN Server (Windows)
echo ========================================================
python -m pip install -r ..\requirements.txt
python tray.py
pause
