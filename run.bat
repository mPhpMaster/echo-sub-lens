@echo off
rem Starts EchoSub Lens from source without a console window.
rem First-time setup:  py -3.10 -m venv .venv  &&  .venv\Scripts\pip install -r requirements.txt
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo EchoSub Lens is not set up yet. Run:
    echo     py -3.10 -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)
start "" .venv\Scripts\pythonw.exe -m echosub_lens %*
