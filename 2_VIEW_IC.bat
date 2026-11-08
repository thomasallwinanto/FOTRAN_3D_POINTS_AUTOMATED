@echo off
REM =====================================================================
REM  STEP 2 :  .ic  ->  .png
REM
REM  Put .ic files in    input\ic\      (or just drag them onto this file)
REM  Images appear in    output\images\
REM
REM  Double-click  -> menu of everything in input\ic\ and output\runs\
REM  Drag & drop   -> renders exactly what you dropped, from anywhere
REM =====================================================================
cd /d "%~dp0"

python "engine\view_particles.py" %* --open

if errorlevel 1 (
    echo.
    echo  ------------------------------------------------------------
    echo   Could not draw the figure. Read the messages above.
    echo   Missing Python packages?  pip install numpy matplotlib
    echo  ------------------------------------------------------------
)
echo.
pause
