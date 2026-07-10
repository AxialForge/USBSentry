@echo off
rem Launch USB Watch with no console window (pythonw = windowless Python).
cd /d "%~dp0"
start "" pythonw "usbwatch.py"
