@echo off
REM =====================================================================
REM  STEP 3 :  .ic  ->  .dat point cloud
REM
REM  Put .ic files in    input\ic\      (or just drag them onto this file)
REM  Clouds appear in    output\points\
REM
REM  Columns written:    X  Y  Z  Object_ID  G_type  Wall_ID
REM  Everything else the IC carries is printed to the console.
REM
REM  Double-click  -> menu of everything in input\ic\ and output\runs\
REM  Drag & drop   -> unpacks exactly what you dropped, from anywhere
REM =====================================================================
cd /d "%~dp0"

python "engine\ic_to_points.py" %*

if errorlevel 1 (
    echo.
    echo  ------------------------------------------------------------
    echo   Could not unpack the file. Read the messages above.
    echo   Missing Python packages?  pip install numpy
    echo  ------------------------------------------------------------
)
echo.
pause
