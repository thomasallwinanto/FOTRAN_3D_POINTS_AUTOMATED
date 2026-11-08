@echo off
REM =====================================================================
REM  STEP 1 :  3D models  ->  GADGET-2 IC file
REM
REM  3D models          ->  input\models\
REM  point clouds (.dat) ->  input\ic\
REM  Results appear in       output\runs\<timestamp>\
REM
REM  Double-click  -> menu of every model AND every point cloud. Pick which
REM                   ones to generate TOGETHER into one IC file; the order
REM                   you type is the stacking order, bottom first.
REM  Drag & drop   -> runs straight on the file(s) you dropped, no menu.
REM
REM  Object 2 is stacked ON TOP of object 1. You are asked how big the gap
REM  should be in mm, and how to line them up. A preview opens at the end.
REM =====================================================================
cd /d "%~dp0"

python "engine\run_pipeline.py" %*

if errorlevel 1 (
    echo.
    echo  ------------------------------------------------------------
    echo   The run did not finish. Read the messages above.
    echo   Missing Python packages?  pip install trimesh numpy scipy matplotlib
    echo  ------------------------------------------------------------
)
echo.
pause
