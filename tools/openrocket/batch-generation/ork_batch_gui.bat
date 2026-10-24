@echo off
cd /d "%~dp0"
start "" pythonw ork_batch_gui.py %* || python ork_batch_gui.py %*
