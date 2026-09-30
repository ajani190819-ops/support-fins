@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ===========================================================================
rem  OrcaSlicer plugin installer / updater  --  Support Fins + Wave Overhangs
rem ===========================================================================
rem
rem  Keep this ONE file anywhere (Downloads, a Tools folder, the Desktop) and
rem  double-click it whenever you want to install or update the plugins. It needs
rem  NO Python, Node, Git or build tools: it downloads the ready-built plugins and
rem  copies them into your OrcaSlicer data folder.
rem
rem  Usage:
rem    install-support-fins-nightly.bat
rem    install-support-fins-nightly.bat "C:\Users\you\AppData\Roaming\OrcaSlicer"
rem
rem  The optional first argument is your Orca data directory. If omitted, the
rem  script finds it under %APPDATA% (preferring a nightly folder) and asks if
rem  there is more than one.
rem
rem  Environment overrides:
rem    ORCA_DATA_DIR   - default Orca data directory (same as the argument)
rem    PLUGIN_BRANCH   - git branch/ref to download from (default below)
rem    PLUGIN_PICK     - 1=Support Fins, 2=Wave Overhangs, 3=Both (skip the menu)
rem ===========================================================================

title OrcaSlicer plugin installer  --  Support Fins + Wave Overhangs

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

if not defined PLUGIN_BRANCH set "PLUGIN_BRANCH=arena/01a0f0b3-support-fins"
set "RAWBASE=https://raw.githubusercontent.com/ajani190819-ops/support-fins/%PLUGIN_BRANCH%/orca-nightly-installer/manual-install"

echo ===========================================================================
echo  OrcaSlicer plugins  --  install / update
echo  (no Python/Node/Git needed; downloads the ready-built plugins)
echo ===========================================================================
echo.

rem --- which plugins? ---
set "PICK=%PLUGIN_PICK%"
if not defined PICK (
    echo Which plugin(s) do you want to install / update?
    echo   [1] Support Fins            ^(stable^)
    echo   [2] Wave Overhangs          ^(experimental^)
    echo   [3] Both                    ^(default^)
    echo.
    set /p PICK=Enter 1, 2 or 3 [3]: 
)
if not defined PICK set "PICK=3"
set "DO_SF=0"
set "DO_WO=0"
if "%PICK%"=="1" set "DO_SF=1"
if "%PICK%"=="2" set "DO_WO=1"
if "%PICK%"=="3" ( set "DO_SF=1" & set "DO_WO=1" )
if "%DO_SF%%DO_WO%"=="00" (
    echo Nothing selected. Exiting.
    goto :fail
)

call :select_data_dir "%~1"
if errorlevel 1 goto :fail

set "PLUGIN_ROOT=%TARGET_DATA_DIR%\orca_plugins"
if not exist "%PLUGIN_ROOT%" mkdir "%PLUGIN_ROOT%"
if errorlevel 1 goto :mkdir_failed

echo.
echo === Installing into: "%PLUGIN_ROOT%" ===

set "SF_OK=0"
set "WO_OK=0"
if "%DO_SF%"=="1" call :get_and_install "Support Fins" "SupportFins" "support_fins_orca.py" "%RAWBASE%/support_fins_orca.py" "%HERE%\manual-install\support_fins_orca.py" "Support Fins" "Support Fins - Check setup" "0.1.0" && set "SF_OK=1"
if "%DO_WO%"=="1" call :get_and_install "Wave Overhangs" "WaveOverhangs" "wave_overhangs_orca.py" "%RAWBASE%/wave-overhangs/wave_overhangs_orca.py" "%HERE%\manual-install\wave-overhangs\wave_overhangs_orca.py" "Wave Overhangs" "Wave Overhangs - Check setup" "0.0.1" && set "WO_OK=1"
set /a OK_COUNT=SF_OK+WO_OK

if "%OK_COUNT%"=="0" (
    echo.
    echo ERROR: nothing was installed.
    goto :fail
)

echo.
echo ===========================================================================
echo  Done. Installed / updated %OK_COUNT% plugin(s).
echo.
echo  NEXT STEPS in OrcaSlicer:
echo    1. FULLY QUIT and reopen OrcaSlicer (deps install on first load).
echo    2. File ^> Plugins  -- confirm the plugin(s) are enabled.
echo    3. Run the "... - Check setup" capability from the Plugins dialog.
echo    4. In your process preset (Advanced): Others ^> Slicing Pipeline Plugin
echo       -- choose "Support Fins" and/or "Wave Overhangs".
echo.
echo  If a plugin does not appear after restart, use File ^> Plugins ^>
echo  Install local plugin and pick the .py the script left in its folder:
echo    "%PLUGIN_ROOT%\SupportFins\support_fins_orca.py"
echo    "%PLUGIN_ROOT%\WaveOverhangs\wave_overhangs_orca.py"
echo ===========================================================================
goto :done


rem ---------------------------------------------------------------------------
rem  get_and_install:
rem    %1 display  %2 subdir  %3 filename  %4 url  %5 localcopy
rem    %6 cap1     %7 cap2    %8 version
rem ---------------------------------------------------------------------------
:get_and_install
set "GI_NAME=%~1"
set "GI_SUBDIR=%~2"
set "GI_FILE=%~3"
set "GI_URL=%~4"
set "GI_LOCAL=%~5"
set "GI_CAP1=%~6"
set "GI_CAP2=%~7"
set "GI_VER=%~8"

echo.
echo --- %GI_NAME% ---
set "GI_SRC="
if exist "%GI_LOCAL%" (
    set "GI_SRC=%GI_LOCAL%"
    echo Using local copy: "%GI_LOCAL%"
) else (
    set "GI_SRC=%TEMP%\%GI_FILE%"
    echo Downloading latest . . .
    call :download "%GI_URL%" "!GI_SRC!"
    if errorlevel 1 (
        echo ERROR: could not download %GI_NAME%.
        echo   URL: %GI_URL%
        echo   Check your internet connection, or install this plugin manually.
        exit /b 1
    )
)

set "GI_DIR=%PLUGIN_ROOT%\%GI_SUBDIR%"
if not exist "%GI_DIR%" mkdir "%GI_DIR%"
if errorlevel 1 (
    echo ERROR: could not create "%GI_DIR%".
    exit /b 1
)
copy /Y "%GI_SRC%" "%GI_DIR%\%GI_FILE%" >nul
if errorlevel 1 (
    echo ERROR: could not copy %GI_NAME% into "%GI_DIR%".
    exit /b 1
)
call :write_state "%GI_DIR%\.install_state.json" "%GI_NAME%" "%GI_CAP1%" "%GI_CAP2%" "%GI_VER%"
echo Installed: "%GI_DIR%\%GI_FILE%"
exit /b 0


rem ---------------------------------------------------------------------------
rem  download %1=url %2=dest   (PowerShell; no curl/git needed)
rem ---------------------------------------------------------------------------
:download
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%~1' -OutFile '%~2' } catch { Write-Host $_.Exception.Message; exit 1 }"
exit /b %ERRORLEVEL%


rem ---------------------------------------------------------------------------
rem  write_state %1=file %2=pluginname %3=cap1 %4=cap2 %5=version
rem ---------------------------------------------------------------------------
:write_state
> "%~1" echo {
>>"%~1" echo   "capabilities": [
>>"%~1" echo     { "%~3": true },
>>"%~1" echo     { "%~4": true }
>>"%~1" echo   ],
>>"%~1" echo   "enabled": true,
>>"%~1" echo   "installed_from": "local",
>>"%~1" echo   "installed_version": "%~5",
>>"%~1" echo   "plugin_name": "%~2"
>>"%~1" echo }
exit /b 0


rem ---------------------------------------------------------------------------
rem  Choose the OrcaSlicer data directory (prefers a nightly folder)
rem ---------------------------------------------------------------------------
:select_data_dir
set "TARGET_DATA_DIR="
if not "%~1"=="" (
    set "TARGET_DATA_DIR=%~1"
    goto :ensure_data_dir
)
if defined ORCA_DATA_DIR (
    set "TARGET_DATA_DIR=%ORCA_DATA_DIR%"
    goto :ensure_data_dir
)
if not defined APPDATA (
    echo ERROR: APPDATA is not set. Pass Orca's data directory as the first argument.
    exit /b 1
)

echo.
echo === Locating your OrcaSlicer data directory ===
set /a NCOUNT=0
set /a OCOUNT=0
for /d %%D in ("%APPDATA%\OrcaSlicer*") do (
    set "NAME=%%~nxD"
    set "IS_NIGHTLY="
    echo(!NAME! | findstr /i "night dev alpha beta" >nul && set "IS_NIGHTLY=1"
    if defined IS_NIGHTLY (
        set /a NCOUNT+=1
        set "NCAND_!NCOUNT!=%%~fD"
    ) else (
        set /a OCOUNT+=1
        set "OCAND_!OCOUNT!=%%~fD"
    )
)
if "%NCOUNT%"=="1" (
    set "TARGET_DATA_DIR=!NCAND_1!"
    echo Found nightly data dir: "!TARGET_DATA_DIR!"
    goto :ensure_data_dir
)
if "%NCOUNT%"=="0" if "%OCOUNT%"=="1" (
    set "TARGET_DATA_DIR=!OCAND_1!"
    echo Found data dir: "!TARGET_DATA_DIR!"
    goto :ensure_data_dir
)
if "%NCOUNT%"=="0" if "%OCOUNT%"=="0" (
    set "TARGET_DATA_DIR=%APPDATA%\OrcaSlicer"
    echo No existing OrcaSlicer data dir found; defaulting to:
    echo   "!TARGET_DATA_DIR!"
    goto :ensure_data_dir
)

echo.
echo Select the data directory to install into:
set /a IDX=0
if not "%NCOUNT%"=="0" echo   -- nightly --
for /L %%I in (1,1,%NCOUNT%) do (
    set /a IDX+=1
    set "MENU_!IDX!=!NCAND_%%I!"
    echo   [!IDX!] !NCAND_%%I!
)
if not "%OCOUNT%"=="0" echo   -- other Orca data dirs --
for /L %%I in (1,1,%OCOUNT%) do (
    set /a IDX+=1
    set "MENU_!IDX!=!OCAND_%%I!"
    echo   [!IDX!] !OCAND_%%I!
)
echo.
set /p DPICK=Enter a number, or type a full path: 
if not defined DPICK exit /b 1
set "TARGET_DATA_DIR="
for /L %%I in (1,1,%IDX%) do (
    if "%DPICK%"=="%%I" set "TARGET_DATA_DIR=!MENU_%%I!"
)
if not defined TARGET_DATA_DIR set "TARGET_DATA_DIR=%DPICK%"

:ensure_data_dir
set "TARGET_DATA_DIR=%TARGET_DATA_DIR:"=%"
if not exist "%TARGET_DATA_DIR%" (
    echo.
    echo Orca data directory does not exist yet:
    echo "%TARGET_DATA_DIR%"
    set /p MAKE_DIR=Create it? [Y/N] 
    if /I not "!MAKE_DIR!"=="Y" exit /b 1
    mkdir "%TARGET_DATA_DIR%"
    if errorlevel 1 exit /b 1
)
exit /b 0

:mkdir_failed
echo ERROR: Could not create the orca_plugins directory.
goto :fail

:fail
echo.
echo Install/update FAILED.
pause
exit /b 1

:done
echo.
pause
exit /b 0
