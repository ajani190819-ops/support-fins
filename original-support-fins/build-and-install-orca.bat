@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Support Fins for OrcaSlicer -- BUILD FROM SOURCE, then install/update.
rem
rem   This is the developer path: it needs Python and Node, and it installs
rem   whatever is in THIS working tree. To just get the newest released
rem   plugins, double-click ..\Install-Orca-Plugins.bat instead -- no build
rem   tools needed, and it does every plugin, not only Support Fins.
rem
rem Usage:
rem   build-and-install-orca.bat
rem   build-and-install-orca.bat "C:\Users\you\AppData\Roaming\OrcaSlicer"
rem
rem The optional first argument is OrcaSlicer's data directory. If omitted, the
rem script looks for OrcaSlicer* folders under %%APPDATA%% and prompts when more
rem than one is found.

title Support Fins Orca plugin installer

set "PROJECT=%~dp0"
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"
set "BUILD=%PROJECT%\plugins\orca\build.py"
set "BUILT_PLUGIN=%PROJECT%\plugins\orca\build\support_fins_orca.py"

if not exist "%BUILD%" (
    echo ERROR: Could not find the Support Fins project around this .bat file.
    echo Expected: "%BUILD%"
    echo.
    echo Keep this .bat inside the original-support-fins folder of the repo.
    goto :fail
)

call :find_python
if errorlevel 1 goto :fail

echo.
echo === Building Support Fins Orca plugin ===
pushd "%PROJECT%" >nul
%PYTHON_CMD% plugins\orca\build.py
set "BUILD_RC=%ERRORLEVEL%"
popd >nul
if not "%BUILD_RC%"=="0" (
    echo.
    echo ERROR: Build failed.
    echo The build needs Python and esbuild. If esbuild is not installed, build.py
    echo will try to use npx to fetch it.
    goto :fail
)

if not exist "%BUILT_PLUGIN%" (
    echo ERROR: Built plugin was not created:
    echo "%BUILT_PLUGIN%"
    goto :fail
)

call :select_data_dir "%~1"
if errorlevel 1 goto :fail

set "PLUGIN_ROOT=%TARGET_DATA_DIR%\orca_plugins"
set "PLUGIN_DIR=%PLUGIN_ROOT%\SupportFins"
set "STATE_FILE=%PLUGIN_DIR%\.install_state.json"

echo.
echo === Installing / updating Orca plugin ===
echo Orca data dir: "%TARGET_DATA_DIR%"
echo Plugin dir:    "%PLUGIN_DIR%"

if not exist "%PLUGIN_ROOT%" mkdir "%PLUGIN_ROOT%"
if errorlevel 1 goto :mkdir_failed
if not exist "%PLUGIN_DIR%" mkdir "%PLUGIN_DIR%"
if errorlevel 1 goto :mkdir_failed

copy /Y "%BUILT_PLUGIN%" "%PLUGIN_DIR%\support_fins_orca.py" >nul
if errorlevel 1 (
    echo ERROR: Could not copy plugin into "%PLUGIN_DIR%".
    goto :fail
)

rem Orca's Plugins dialog writes this sidecar when installing locally. Writing it
rem here makes direct installs/updates discoverable and enabled without having to
rem use the UI installer every time.
> "%STATE_FILE%" (
    echo {
    echo   "capabilities": [
    echo     { "Support Fins": true },
    echo     { "Support Fins - Check setup": true }
    echo   ],
    echo   "enabled": true,
    echo   "installed_from": "local",
    echo   "installed_version": "0.1.0",
    echo   "plugin_name": "Support Fins"
    echo }
)
if errorlevel 1 (
    echo ERROR: Could not write "%STATE_FILE%".
    goto :fail
)

echo.
echo Installed / updated:
echo   "%PLUGIN_DIR%\support_fins_orca.py"
echo   "%STATE_FILE%"
echo.
echo The plugin contains both:
echo   - Support Fins                  ^(slicing-pipeline fin injector^)
echo   - Support Fins - Check setup    ^(Plugins-dialog script smoke test^)
echo.
echo Next steps in OrcaSlicer:
echo   1. Restart OrcaSlicer, or reopen File ^> Plugins if it is already running.
echo   2. Confirm Support Fins is enabled.
echo   3. Load a model and run "Support Fins - Check setup" from the Plugins dialog.
echo   4. In your process preset, choose "Support Fins" under
echo      Others ^> Slicing Pipeline Plugin.
echo.
goto :done

:find_python
set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo ERROR: Python was not found on PATH.
    echo Install Python 3, or install Orca's plugin build from a shell where python works.
    exit /b 1
)
%PYTHON_CMD% --version
exit /b 0

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

set /a CAND_COUNT=0
for /d %%D in ("%APPDATA%\OrcaSlicer*") do (
    set /a CAND_COUNT+=1
    set "CAND_!CAND_COUNT!=%%~fD"
)

if "%CAND_COUNT%"=="0" (
    set "TARGET_DATA_DIR=%APPDATA%\OrcaSlicer"
    goto :ensure_data_dir
)
if "%CAND_COUNT%"=="1" (
    set "TARGET_DATA_DIR=!CAND_1!"
    goto :ensure_data_dir
)

echo.
echo Found multiple Orca data directories:
for /L %%I in (1,1,%CAND_COUNT%) do echo   [%%I] !CAND_%%I!
echo.
set /p PICK=Install/update which one? Enter number, or a full path: 
if not defined PICK exit /b 1

set "TARGET_DATA_DIR="
for /L %%I in (1,1,%CAND_COUNT%) do (
    if "%PICK%"=="%%I" set "TARGET_DATA_DIR=!CAND_%%I!"
)
if not defined TARGET_DATA_DIR set "TARGET_DATA_DIR=%PICK%"

:ensure_data_dir
set "TARGET_DATA_DIR=%TARGET_DATA_DIR:"=%"
if not exist "%TARGET_DATA_DIR%" (
    echo Orca data directory does not exist yet:
    echo "%TARGET_DATA_DIR%"
    set /p MAKE_DIR=Create it? [Y/N] 
    if /I not "!MAKE_DIR!"=="Y" exit /b 1
    mkdir "%TARGET_DATA_DIR%"
    if errorlevel 1 exit /b 1
)
exit /b 0

:mkdir_failed
echo ERROR: Could not create plugin directories.
goto :fail

:fail
echo.
echo Install/update failed.
pause
exit /b 1

:done
echo Install/update complete.
pause
exit /b 0
