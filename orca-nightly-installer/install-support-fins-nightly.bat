@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ===========================================================================
rem  Support Fins for OrcaSlicer  -  ONE-STEP install/update for the NIGHTLY build
rem ===========================================================================
rem
rem  This is the "install and update everything" bootstrapper for Windows.
rem  Double-click it (or run it from a terminal) and it will:
rem
rem    1. Make sure the build prerequisites are present (Git, Python, Node.js),
rem       installing any that are missing via winget.
rem    2. Get / update the Support Fins source (git pull an existing checkout,
rem       or clone a cached copy under %LOCALAPPDATA%).
rem    3. Build the single-file Orca plugin from the current source.
rem    4. Find your OrcaSlicer *nightly* data directory (preferring nightly /
rem       dev / alpha / beta folders under %APPDATA%).
rem    5. Copy the plugin into <data_dir>\orca_plugins\SupportFins\ and write
rem       Orca's .install_state.json so both capabilities are enabled.
rem
rem  Usage:
rem    install-support-fins-nightly.bat
rem    install-support-fins-nightly.bat "C:\Users\you\AppData\Roaming\OrcaSlicerNightly"
rem
rem  The optional first argument is the Orca data directory (or a portable
rem  data_dir next to your nightly OrcaSlicer.exe). If omitted, the script
rem  auto-detects and prompts when there is more than one candidate.
rem
rem  Environment overrides:
rem    ORCA_DATA_DIR      - default Orca data directory (same as the argument)
rem    SUPPORT_FINS_REPO  - git URL to clone when no local checkout is found
rem                         (default: https://github.com/ajani190819-ops/support-fins.git)
rem    SKIP_PREREQS=1     - do not try to install Git/Python/Node via winget
rem    SKIP_UPDATE=1      - do not git pull/clone; just build what is on disk
rem ===========================================================================

title Support Fins - OrcaSlicer nightly installer

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

rem The repository root is the parent of this folder when run from a checkout.
for %%I in ("%HERE%\..") do set "REPO_ROOT=%%~fI"

if not defined SUPPORT_FINS_REPO set "SUPPORT_FINS_REPO=https://github.com/ajani190819-ops/support-fins.git"
set "CACHE_ROOT=%LOCALAPPDATA%\SupportFinsOrca"
set "CACHE_REPO=%CACHE_ROOT%\support-fins"

echo ===========================================================================
echo  Support Fins for OrcaSlicer  --  nightly install / update
echo ===========================================================================
echo.

call :ensure_prereqs
if errorlevel 1 goto :fail

call :resolve_project
if errorlevel 1 goto :fail

set "BUILD=%PROJECT%\plugins\orca\build.py"
set "BUILT_PLUGIN=%PROJECT%\plugins\orca\build\support_fins_orca.py"

if not exist "%BUILD%" (
    echo ERROR: Could not find the Support Fins project build script.
    echo Expected: "%BUILD%"
    goto :fail
)

echo.
echo === Building Support Fins Orca plugin ===
pushd "%PROJECT%" >nul
%PYTHON_CMD% plugins\orca\build.py
set "BUILD_RC=%ERRORLEVEL%"
popd >nul
if not "%BUILD_RC%"=="0" (
    echo.
    echo ERROR: Build failed.
    echo The build needs Python and esbuild. esbuild is fetched with npx, so Node.js
    echo must be installed and reachable. Re-run after installing Node.js if needed.
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
echo Next steps in OrcaSlicer (nightly):
echo   1. Restart OrcaSlicer, or reopen File ^> Plugins if it is already running.
echo   2. Confirm Support Fins is enabled.
echo   3. Load a model and run "Support Fins - Check setup" from the Plugins dialog.
echo   4. In your process preset, choose "Support Fins" under
echo      Others ^> Slicing Pipeline Plugin.
echo.
goto :done


rem ---------------------------------------------------------------------------
rem  Prerequisites: Git, Python, Node.js (for npx/esbuild)
rem ---------------------------------------------------------------------------
:ensure_prereqs
echo === Checking prerequisites ===

set "HAVE_WINGET="
where winget >nul 2>nul && set "HAVE_WINGET=1"

rem --- Git ---
call :have_git
if errorlevel 1 (
    if defined SKIP_PREREQS (
        echo ERROR: Git not found and SKIP_PREREQS is set.
        exit /b 1
    )
    call :winget_install "Git.Git" "Git"
    call :add_to_path "%ProgramFiles%\Git\cmd"
    call :add_to_path "%ProgramFiles%\Git\bin"
    call :have_git
    if errorlevel 1 (
        echo NOTE: Git still not on PATH in this window. If a checkout already
        echo       exists on disk the script can continue; otherwise close this
        echo       window and run the installer again.
    )
)

rem --- Python ---
call :find_python
if errorlevel 1 (
    if defined SKIP_PREREQS (
        echo ERROR: Python not found and SKIP_PREREQS is set.
        exit /b 1
    )
    call :winget_install "Python.Python.3.12" "Python 3"
    call :add_to_path "%LOCALAPPDATA%\Programs\Python\Python312"
    call :add_to_path "%LOCALAPPDATA%\Programs\Python\Python312\Scripts"
    call :find_python
    if errorlevel 1 (
        echo ERROR: Python is required to build the plugin and was not found on PATH.
        echo        Close this window and run the installer again so a freshly
        echo        installed Python is picked up, or install Python 3 manually.
        exit /b 1
    )
)
echo Python: & %PYTHON_CMD% --version

rem --- Node.js / npx (needed by build.py to fetch esbuild) ---
call :have_node
if errorlevel 1 (
    if defined SKIP_PREREQS (
        echo NOTE: Node.js not found and SKIP_PREREQS is set. Build needs esbuild;
        echo       set ESBUILD=path\to\esbuild.exe if you have it another way.
    ) else (
        call :winget_install "OpenJS.NodeJS.LTS" "Node.js LTS"
        call :add_to_path "%ProgramFiles%\nodejs"
        call :have_node
        if errorlevel 1 (
            echo NOTE: Node.js still not on PATH in this window. If the build fails
            echo       to fetch esbuild, close this window and re-run the installer.
        )
    )
)
exit /b 0

:have_git
where git >nul 2>nul
exit /b %ERRORLEVEL%

:have_node
where npx >nul 2>nul && exit /b 0
where node >nul 2>nul
exit /b %ERRORLEVEL%

:find_python
set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD exit /b 1
exit /b 0

:winget_install
rem %~1 = winget package id, %~2 = friendly name
if not defined HAVE_WINGET (
    echo NOTE: %~2 not found and winget is unavailable. Please install %~2 manually.
    exit /b 0
)
echo Installing %~2 via winget (%~1) . . .
winget install --id %~1 -e --source winget --accept-source-agreements --accept-package-agreements --disable-interactivity
exit /b 0

:add_to_path
rem Add a directory to PATH for this session only, if it exists.
if exist "%~1" set "PATH=%~1;%PATH%"
exit /b 0


rem ---------------------------------------------------------------------------
rem  Resolve the Support Fins project folder (checkout in repo, or cached clone)
rem ---------------------------------------------------------------------------
:resolve_project
set "PROJECT="

rem 1. Running from inside a checkout: <repo>\support-fins next to this folder.
if exist "%REPO_ROOT%\support-fins\plugins\orca\build.py" (
    set "PROJECT=%REPO_ROOT%\support-fins"
    echo === Using local checkout ===
    echo Project: "!PROJECT!"
    if not defined SKIP_UPDATE (
        call :have_git
        if not errorlevel 1 (
            if exist "%REPO_ROOT%\.git" (
                echo Updating checkout (git pull --ff-only) . . .
                git -C "%REPO_ROOT%" pull --ff-only
                if errorlevel 1 echo NOTE: git pull did not complete cleanly; building current files.
            )
        )
    )
    exit /b 0
)

rem 2. Otherwise use / refresh a cached clone under %LOCALAPPDATA%.
if defined SKIP_UPDATE (
    if exist "%CACHE_REPO%\support-fins\plugins\orca\build.py" (
        set "PROJECT=%CACHE_REPO%\support-fins"
        echo === Using cached checkout (update skipped) ===
        echo Project: "!PROJECT!"
        exit /b 0
    )
    echo ERROR: SKIP_UPDATE is set but no source was found on disk.
    exit /b 1
)

call :have_git
if errorlevel 1 (
    echo ERROR: Git is required to fetch the Support Fins source and was not found.
    echo        Install Git (or re-run this installer from inside a repo checkout),
    echo        then try again.
    exit /b 1
)

if exist "%CACHE_REPO%\.git" (
    echo === Updating cached source ===
    echo Repo: "%CACHE_REPO%"
    git -C "%CACHE_REPO%" pull --ff-only
    if errorlevel 1 echo NOTE: git pull did not complete cleanly; building current files.
) else (
    echo === Cloning Support Fins source ===
    echo From: %SUPPORT_FINS_REPO%
    echo Into: "%CACHE_REPO%"
    if not exist "%CACHE_ROOT%" mkdir "%CACHE_ROOT%"
    git clone --depth 1 "%SUPPORT_FINS_REPO%" "%CACHE_REPO%"
    if errorlevel 1 (
        echo ERROR: git clone failed.
        exit /b 1
    )
)

if not exist "%CACHE_REPO%\support-fins\plugins\orca\build.py" (
    echo ERROR: Cloned source does not contain the expected project layout.
    echo Expected: "%CACHE_REPO%\support-fins\plugins\orca\build.py"
    exit /b 1
)
set "PROJECT=%CACHE_REPO%\support-fins"
echo Project: "%PROJECT%"
exit /b 0


rem ---------------------------------------------------------------------------
rem  Choose the OrcaSlicer NIGHTLY data directory
rem ---------------------------------------------------------------------------
:select_data_dir
set "TARGET_DATA_DIR="

rem Explicit argument or env var wins.
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
echo === Locating your OrcaSlicer nightly data directory ===

rem Build two lists: nightly-looking candidates first, then any other Orca dirs.
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

rem Exactly one nightly candidate -> use it.
if "%NCOUNT%"=="1" (
    set "TARGET_DATA_DIR=!NCAND_1!"
    echo Found nightly data dir: "!TARGET_DATA_DIR!"
    goto :ensure_data_dir
)

rem No dirs at all -> sensible default.
if "%NCOUNT%"=="0" if "%OCOUNT%"=="0" (
    set "TARGET_DATA_DIR=%APPDATA%\OrcaSlicer"
    echo No existing OrcaSlicer data dir found; defaulting to:
    echo   "!TARGET_DATA_DIR!"
    goto :ensure_data_dir
)

rem Present a menu (nightly first), then stable, then allow a custom path.
echo.
echo Select the data directory to install into
echo (nightly builds usually store data in a folder whose name contains "nightly"):
echo.
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
set /p PICK=Enter a number, or type a full path (e.g. a portable data_dir): 
if not defined PICK exit /b 1

set "TARGET_DATA_DIR="
for /L %%I in (1,1,%IDX%) do (
    if "%PICK%"=="%%I" set "TARGET_DATA_DIR=!MENU_%%I!"
)
if not defined TARGET_DATA_DIR set "TARGET_DATA_DIR=%PICK%"
goto :ensure_data_dir

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
echo ERROR: Could not create plugin directories.
goto :fail

:fail
echo.
echo Install/update FAILED.
pause
exit /b 1

:done
echo Install/update complete.
pause
exit /b 0
