@echo off
REM ============================================================================
REM  Run StreamTuner-ng FROM SOURCE on Windows (no build needed -- fastest way
REM  to test). First run sets up a venv + installs deps; later runs are instant.
REM
REM  Prerequisites:
REM    1. Python 3.10+  (https://www.python.org/downloads/  -- tick "Add to PATH")
REM    2. libmpv-2.dll  in THIS folder  (see BUILD_WINDOWS.txt) -- needed for audio
REM ============================================================================
setlocal
cd /d "%~dp0"

if not exist ".venv-win\Scripts\activate.bat" (
    echo First run: creating venv and installing dependencies...
    py -3 -m venv .venv-win 2>nul || python -m venv .venv-win
    call ".venv-win\Scripts\activate.bat"
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
) else (
    call ".venv-win\Scripts\activate.bat"
)

REM let python-mpv find a libmpv DLL sitting next to this script
set "PATH=%CD%;%PATH%"
if not exist "libmpv-2.dll" if not exist "mpv-2.dll" (
    echo.
    echo  *** Note: libmpv-2.dll not found here -- the app will run but show "no audio engine".
    echo  ***       Drop libmpv-2.dll in this folder for sound ^(see BUILD_WINDOWS.txt^).
    echo.
)
python run.py %*
