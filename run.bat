@echo off
rem Start the app from a local venv if there is one, otherwise use the system Python.
rem (%~dp0 = this folder, so no hard-coded drive letter is needed.)
cd /d "%~dp0"

if exist ".\venv\Scripts\python.exe" (
    ".\venv\Scripts\python.exe" main.py
) else (
    python main.py
)

pause
