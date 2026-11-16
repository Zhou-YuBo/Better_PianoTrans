@echo off
setlocal
set "PACKAGE_ROOT=%~dp0"
set "PYTHONHOME=%PACKAGE_ROOT%runtime"
set "PYTHONPATH=%PACKAGE_ROOT%app\src"
set "PIANOTRANS_CHECKPOINT=%PACKAGE_ROOT%app\checkpoints\note_F1=0.9677_pedal_F1=0.9186.pth"
set "PIANOTRANS_SOUNDFONT=%PACKAGE_ROOT%app\components\soundfont.sf3"
set "FLUIDSYNTH_BIN_DIR=%PACKAGE_ROOT%app\components\fluidsynth"
set "LAUNCH_ARGS=%*"
if /I "%~1"=="--installed" goto installed
set "PIANOTRANS_PRODUCT_DB=%PACKAGE_ROOT%data\product-v1.db"
set "STATE_ROOT=%PACKAGE_ROOT%"
goto state
:installed
set "LAUNCH_ARGS="
set "STATE_ROOT=%LOCALAPPDATA%\BetterPianoTrans\"
set "PIANOTRANS_PRODUCT_DB=%STATE_ROOT%product-v1.db"
:state
if not exist "%STATE_ROOT%cache" mkdir "%STATE_ROOT%cache"
if not exist "%STATE_ROOT%temp" mkdir "%STATE_ROOT%temp"
set "PIANOTRANS_TEMP_DIR=%STATE_ROOT%temp"
set "MPLCONFIGDIR=%STATE_ROOT%cache\matplotlib"
set "PYTHONPYCACHEPREFIX=%STATE_ROOT%cache\pycache"
set "NUMBA_CACHE_DIR=%STATE_ROOT%cache\numba"
set "XDG_CACHE_HOME=%STATE_ROOT%cache"
set "TORCH_HOME=%STATE_ROOT%cache\torch"
set "TMP=%STATE_ROOT%temp"
set "TEMP=%STATE_ROOT%temp"
set "PATH=%PACKAGE_ROOT%app\components\ffmpeg;%PACKAGE_ROOT%app\components\fluidsynth;%PACKAGE_ROOT%runtime;%PACKAGE_ROOT%runtime\DLLs;%PATH%"
"%PACKAGE_ROOT%runtime\python.exe" -m pianotrans_gui.workbench %LAUNCH_ARGS%
if errorlevel 1 pause
