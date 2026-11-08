@echo off
REM =====================================================================
REM  3D VOX STUDIO  -  the desktop window for everything
REM
REM  Double-click          -> opens the window
REM  Drag files onto it    -> opens with those files already selected
REM
REM  Pick a role on the left:
REM     Quick Start   pick inputs, type a gap, press Generate
REM     Engineer      every option, presets, command line
REM     Analyst       inspect, render, 3D view, export, unpack, compare
REM     Admin         setup check, Fortran, run manager, disk clean-up
REM
REM  The numbered .bat files still work exactly as before (console mode).
REM =====================================================================
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "engine\app.py" %*
    exit /b 0
)

python "engine\app.py" %*
if errorlevel 1 (
    echo.
    echo  ------------------------------------------------------------
    echo   The window could not start. Read the messages above.
    echo   Missing Python packages?  pip install numpy trimesh scipy matplotlib
    echo  ------------------------------------------------------------
    pause
)
