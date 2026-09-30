@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ===========================================================================
rem   Install / update ALL my OrcaSlicer plugins  --  one double-click
rem ===========================================================================
rem
rem   Keep this ONE file wherever you like - Downloads, Desktop, a Tools folder -
rem   and double-click it whenever you want the newest plugins. It needs NO
rem   Python, Node, Git or build tools.
rem
rem   Every run:
rem     1. downloads the plugin catalogue, my-plugins/plugins.json, from GitHub
rem     2. downloads every plugin the catalogue marks as ready
rem     3. installs each into your OrcaSlicer data folder - creating it if you
rem        do not have that plugin yet, overwriting it if you do
rem
rem   Exactly one URL is baked into this file: the catalogue. New plugins are
rem   picked up automatically once they are added there, so this file should
rem   never need replacing.
rem
rem   Usage:
rem     Install-Orca-Plugins.bat
rem     Install-Orca-Plugins.bat "C:\Users\you\AppData\Roaming\OrcaSlicer"
rem     Install-Orca-Plugins.bat --local      use the files next to this .bat
rem     Install-Orca-Plugins.bat --help
rem
rem   Environment overrides:
rem     ORCA_DATA_DIR   default Orca data directory, same as the argument
rem     PLUGIN_BRANCH   git branch/tag to pull from, skips the default search
rem     PLUGIN_ONLY     comma-separated plugin ids, e.g. "unlayered-infill"
rem ===========================================================================

title Install / update my OrcaSlicer plugins

set "REPO=ajani190819-ops/orca-plugins"
set "MANIFEST_PATH=plugins.json"

rem Refs are tried in order. "main" comes first so that once this work is merged
rem the copy of this .bat already sitting on your laptop switches to main by
rem itself - nothing to edit, nothing to re-download.
set "REF_1=main"
set "REF_2="
if defined PLUGIN_BRANCH (
    set "REF_1=%PLUGIN_BRANCH%"
    set "REF_2="
)

set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

set "MANIFEST_WIN=%MANIFEST_PATH:/=\%"
set "LOCAL_MANIFEST=%HERE%\%MANIFEST_WIN%"
set "WORK=%TEMP%\OrcaPluginInstall"
set "DLDIR=%USERPROFILE%\Downloads\OrcaPlugins"
set "PF_MANIFEST=%WORK%\plugins.json"
set "PF_PLAN=%WORK%\plan.txt"
set "PF_WORK=%WORK%"
set "SUMMARY=%WORK%\summary.txt"
set "USE_LOCAL=0"
set "ARG_DATA_DIR="
set "ACTIVE_REF="

rem --- arguments -------------------------------------------------------------
:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--local" (
    set "USE_LOCAL=1"
    shift
    goto parse_args
)
if /i "%~1"=="-l" (
    set "USE_LOCAL=1"
    shift
    goto parse_args
)
if /i "%~1"=="--help" goto show_help
if /i "%~1"=="-h" goto show_help
if /i "%~1"=="/?" goto show_help
set "ARG_DATA_DIR=%~1"
shift
goto parse_args
:args_done

echo ===========================================================================
echo   OrcaSlicer plugins  --  install / update everything
echo ===========================================================================
echo.

if exist "%WORK%" rd /s /q "%WORK%" 2>nul
mkdir "%WORK%" 2>nul
if not exist "%WORK%" (
    echo ERROR: could not create a temporary folder at "%WORK%".
    goto fail
)
type nul > "%SUMMARY%"
if not exist "%DLDIR%" mkdir "%DLDIR%" 2>nul

rem --- 1. the catalogue ------------------------------------------------------
if "%USE_LOCAL%"=="1" goto manifest_local

echo === Looking up the plugin catalogue ===
call :fetch_manifest "%REF_1%"
if not errorlevel 1 goto manifest_ready
if defined REF_2 (
    call :fetch_manifest "%REF_2%"
    if not errorlevel 1 goto manifest_ready
)

echo.
echo Could not download the catalogue from GitHub.
if not exist "%LOCAL_MANIFEST%" (
    echo Check your internet connection and try again.
    goto fail
)
echo Falling back to the copy next to this .bat.
set "USE_LOCAL=1"

:manifest_local
if not exist "%LOCAL_MANIFEST%" (
    echo ERROR: no catalogue at "%LOCAL_MANIFEST%".
    echo Run this .bat from a checkout of the repository, or drop --local so it
    echo can download the plugins instead.
    goto fail
)
copy /Y "%LOCAL_MANIFEST%" "%PF_MANIFEST%" >nul
echo Using the local catalogue: "%LOCAL_MANIFEST%"

:manifest_ready

rem --- 2. work out what to install -------------------------------------------
call :build_plan
if errorlevel 1 goto fail

rem --- 3. where does it go ---------------------------------------------------
call :select_data_dir "%ARG_DATA_DIR%"
if errorlevel 1 goto fail

set "PLUGIN_ROOT=%TARGET_DATA_DIR%\orca_plugins"
if not exist "%PLUGIN_ROOT%" mkdir "%PLUGIN_ROOT%" 2>nul
if not exist "%PLUGIN_ROOT%" (
    echo ERROR: could not create "%PLUGIN_ROOT%".
    goto fail
)

echo.
echo === Installing into "%PLUGIN_ROOT%" ===

rem --- 4. install every plugin in the plan ------------------------------------
set /a N_OK=0
set /a N_NEW=0
set /a N_FAIL=0
for /f "usebackq eol=# tokens=1-6 delims=|" %%a in ("%PF_PLAN%") do call :install_one "%%a" "%%b" "%%c" "%%d" "%%e" "%%f"

echo.
echo ===========================================================================
if %N_OK% GTR 0 echo   Done. Installed or updated %N_OK% plugin file^(s^).
if %N_OK%==0 echo   Nothing was installed.
if %N_FAIL% GTR 0 echo   %N_FAIL% plugin^(s^) FAILED - see the log above.
echo ===========================================================================
echo.
for /f "usebackq delims=" %%L in ("%SUMMARY%") do echo   %%L
if "%USE_LOCAL%"=="1" echo   source   local files beside this .bat
if not "%USE_LOCAL%"=="1" echo   source   %REPO% @ %ACTIVE_REF%
echo.
if %N_OK%==0 goto fail

echo   Next, in OrcaSlicer:
echo     1. FULLY QUIT and reopen OrcaSlicer. Dependencies install on first load.
echo     2. File - Plugins: confirm the plugins are listed and enabled.
echo     3. Run each "... - Check setup" capability from that dialog.
echo     4. Process preset, Advanced: Others - Slicing Pipeline Plugin, then
echo        pick the plugin you want for this print.
echo.
if %N_NEW% GTR 0 goto new_plugin_hint
goto done

:new_plugin_hint
echo   Something here is new. Some OrcaSlicer builds only register a plugin when
echo   it is added through the UI, so if it does not appear after a restart:
echo     File - Plugins - arrow beside "Browse plugins" - "Install local plugin"
echo   and pick the file from the folder opening now:
echo     "%DLDIR%"
echo.
start "" "%DLDIR%" 2>nul
goto done


rem ===========================================================================
rem   fetch_manifest %1=ref   -- sets ACTIVE_REF on success
rem ===========================================================================
:fetch_manifest
set "FM_REF=%~1"
if not defined FM_REF exit /b 1
echo   trying ref "%FM_REF%" . . .
call :download "https://raw.githubusercontent.com/%REPO%/%FM_REF%/%MANIFEST_PATH%" "%PF_MANIFEST%" 40
if errorlevel 1 (
    echo     not found on "%FM_REF%".
    exit /b 1
)
set "ACTIVE_REF=%FM_REF%"
echo     using ref "%FM_REF%".
exit /b 0


rem ===========================================================================
rem   build_plan -- turn plugins.json into plan.txt, one line per plugin, and
rem   write each plugin's .install_state.json sidecar into %WORK%.
rem   Line format:  id|name|version|orca_dir|file|repo_path
rem ===========================================================================
:build_plan
del "%PF_PLAN%" 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $j = Get-Content -LiteralPath $env:PF_MANIFEST -Raw | ConvertFrom-Json } catch { exit 3 }; $only = @(); if ($env:PLUGIN_ONLY) { $only = @($env:PLUGIN_ONLY -split '[,; ]+' | Where-Object { $_ }) }; $lines = @(); foreach ($p in $j.plugins) { if ($p.status -ne 'ready') { continue }; if ($only.Count -gt 0 -and $only -notcontains $p.id) { continue }; $caps = @(); foreach ($c in $p.capabilities) { $caps += @{ $c = $true } }; $state = @{ capabilities = $caps; enabled = $true; installed_from = 'local'; installed_version = [string]$p.version; plugin_name = [string]$p.name }; ($state | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath (Join-Path $env:PF_WORK ($p.id + '.state.json')) -Encoding ASCII; $lines += ('{0}|{1}|{2}|{3}|{4}|{5}' -f $p.id, $p.name, $p.version, $p.orca_dir, $p.file, $p.path) }; Set-Content -LiteralPath (Join-Path $env:PF_WORK 'meta.txt') -Value ([string]$j.updated) -Encoding ASCII; if ($lines.Count -eq 0) { exit 4 }; Set-Content -LiteralPath $env:PF_PLAN -Value $lines -Encoding ASCII"
set "PS_RC=%ERRORLEVEL%"

if "%PS_RC%"=="4" goto plan_empty
if "%PS_RC%"=="0" goto plan_show
echo.
echo   Could not read the catalogue with PowerShell - using the built-in list.
call :fallback_plan

:plan_show
if not exist "%PF_PLAN%" (
    echo ERROR: could not work out what to install.
    exit /b 1
)
set "CAT_UPDATED="
if exist "%WORK%\meta.txt" for /f "usebackq delims=" %%U in ("%WORK%\meta.txt") do set "CAT_UPDATED=%%U"

echo.
echo === Source ===
if "%USE_LOCAL%"=="1" goto src_local
echo   GitHub     %REPO%
echo   Branch/tag %ACTIVE_REF%
goto src_stamp
:src_local
echo   Local files beside this .bat
echo   %HERE%
:src_stamp
if defined CAT_UPDATED echo   Catalogue  last updated %CAT_UPDATED%
if "%USE_LOCAL%"=="1" goto src_done
if /i "%ACTIVE_REF%"=="main" goto src_done
echo.
echo   NOTE: that is a work branch, not main. Expected before the change is
echo   merged. This installer always tries main FIRST, so the moment the work
echo   lands on main it switches over on its own - nothing for you to edit.
:src_done

echo.
echo === Plugins to install / update ===
for /f "usebackq eol=# tokens=1-3 delims=|" %%a in ("%PF_PLAN%") do echo   - %%b  v%%c
exit /b 0

:plan_empty
echo.
echo No ready plugins matched.
if defined PLUGIN_ONLY echo PLUGIN_ONLY is "%PLUGIN_ONLY%" - clear it to install everything.
exit /b 1


rem ===========================================================================
rem   fallback_plan -- hard-coded list, used only if PowerShell cannot parse the
rem   catalogue. Keeps this file working rather than failing outright.
rem ===========================================================================
:fallback_plan
> "%PF_PLAN%" echo wave-overhangs^|Wave Overhangs^|0.0.3^|WaveOverhangs^|wave_overhangs_orca.py^|wave-overhangs/wave_overhangs_orca.py
>>"%PF_PLAN%" echo unlayered-infill^|Unlayered Infill^|0.2.0^|UnlayeredInfill^|unlayered_infill_orca.py^|unlayered-infill/unlayered_infill_orca.py
call :write_state "%WORK%\wave-overhangs.state.json" "Wave Overhangs" "Wave Overhangs" "Wave Overhangs - Check setup" "0.0.3"
call :write_state "%WORK%\unlayered-infill.state.json" "Unlayered Infill" "Unlayered Infill" "Unlayered Infill - Check setup" "0.2.0"
exit /b 0


rem ===========================================================================
rem   install_one %1=id %2=name %3=version %4=orca_dir %5=file %6=repo_path
rem ===========================================================================
:install_one
set "P_ID=%~1"
set "P_NAME=%~2"
set "P_VER=%~3"
set "P_DIR=%~4"
set "P_FILE=%~5"
set "P_PATH=%~6"
set "P_PATH_WIN=%P_PATH:/=\%"

echo.
echo --- %P_NAME% v%P_VER% ---

set "SRC=%WORK%\%P_FILE%"
set "LOCAL_COPY=%HERE%\%P_PATH_WIN%"
if "%USE_LOCAL%"=="1" goto install_from_local

call :download "https://raw.githubusercontent.com/%REPO%/%ACTIVE_REF%/%P_PATH%" "%SRC%" 2000
if errorlevel 1 (
    echo   [FAIL] could not download %P_NAME%.
    set /a N_FAIL+=1
    exit /b 1
)
goto install_have_src

:install_from_local
if not exist "%LOCAL_COPY%" (
    echo   [FAIL] local copy missing: "%LOCAL_COPY%"
    set /a N_FAIL+=1
    exit /b 1
)
copy /Y "%LOCAL_COPY%" "%SRC%" >nul
echo   from "%LOCAL_COPY%"

:install_have_src
rem Keep a copy where Orca's "Install local plugin" file browser can reach it.
copy /Y "%SRC%" "%DLDIR%\%P_FILE%" >nul 2>nul

set "DEST_DIR=%PLUGIN_ROOT%\%P_DIR%"
set "WAS_THERE=0"
if exist "%DEST_DIR%\%P_FILE%" set "WAS_THERE=1"
if not exist "%DEST_DIR%" mkdir "%DEST_DIR%" 2>nul
if not exist "%DEST_DIR%" (
    echo   [FAIL] could not create "%DEST_DIR%".
    set /a N_FAIL+=1
    exit /b 1
)

copy /Y "%SRC%" "%DEST_DIR%\%P_FILE%" >nul
if errorlevel 1 (
    echo   [FAIL] could not write "%DEST_DIR%\%P_FILE%".
    echo          Is OrcaSlicer still running? Close it fully and run this again.
    set /a N_FAIL+=1
    exit /b 1
)
if exist "%WORK%\%P_ID%.state.json" copy /Y "%WORK%\%P_ID%.state.json" "%DEST_DIR%\.install_state.json" >nul

rem Update any other copy of this plugin already installed under a different
rem folder name - for example one added earlier through Orca's UI installer.
call :update_siblings "%P_FILE%" "%DEST_DIR%"

set /a N_OK+=1
if "%WAS_THERE%"=="1" goto install_was_update
set /a N_NEW+=1
for %%A in ("%DEST_DIR%\%P_FILE%") do echo   [INSTALLED] %%~zA bytes into "%DEST_DIR%"
>>"%SUMMARY%" echo NEW      %P_NAME% v%P_VER%   in   %DEST_DIR%
exit /b 0

:install_was_update
for %%A in ("%DEST_DIR%\%P_FILE%") do echo   [UPDATED] %%~zA bytes into "%DEST_DIR%"
>>"%SUMMARY%" echo updated  %P_NAME% v%P_VER%   in   %DEST_DIR%
exit /b 0


rem ===========================================================================
rem   update_siblings %1=filename %2=directory already handled
rem ===========================================================================
:update_siblings
for /f "usebackq delims=" %%F in (`dir /b /s "%PLUGIN_ROOT%\%~1" 2^>nul`) do call :update_sibling "%%F" "%~2"
exit /b 0

:update_sibling
set "SIB_DIR=%~dp1"
if "%SIB_DIR:~-1%"=="\" set "SIB_DIR=%SIB_DIR:~0,-1%"
if /i "%SIB_DIR%"=="%~2" exit /b 0
echo "%SIB_DIR%" | findstr /i "_subscribed" >nul
if not errorlevel 1 goto sibling_is_cloud
copy /Y "%SRC%" "%~1" >nul 2>nul
if not errorlevel 1 echo   also updated an existing copy in "%SIB_DIR%"
exit /b 0

:sibling_is_cloud
echo   [NOTE] a cloud/subscribed copy also exists and was left alone:
echo       "%SIB_DIR%"
echo       Uninstall it from File - Plugins if the plugin misbehaves.
exit /b 0


rem ===========================================================================
rem   download %1=url %2=dest %3=minimum acceptable bytes
rem   Tries curl.exe, then PowerShell, then BITS.
rem ===========================================================================
:download
del "%~2" 2>nul
set "DL_MIN=%~3"
if not defined DL_MIN set "DL_MIN=200"
set "DL_DEST=%~2"
rem Cache-buster: raw.githubusercontent is CDN-cached and this has to fetch the
rem newest file, not whatever an edge node is still holding.
set "DL_URL=%~1?cb=%RANDOM%%RANDOM%"

where curl.exe >nul 2>nul
if errorlevel 1 goto dl_powershell
curl.exe -fLsS --retry 2 -H "Cache-Control: no-cache" -o "%DL_DEST%" "%DL_URL%" 2>nul
if not errorlevel 1 if exist "%DL_DEST%" goto download_check

:dl_powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri $env:DL_URL -OutFile $env:DL_DEST } catch { exit 1 }" 2>nul
if not errorlevel 1 if exist "%DL_DEST%" goto download_check

bitsadmin /transfer OrcaPluginDL /priority foreground "%DL_URL%" "%DL_DEST%" >nul 2>nul
if exist "%DL_DEST%" goto download_check
exit /b 1

:download_check
rem Reject empty or truncated files, e.g. a 404 page saved as the plugin.
for %%A in ("%DL_DEST%") do if %%~zA GEQ %DL_MIN% exit /b 0
del "%DL_DEST%" 2>nul
exit /b 1


rem ===========================================================================
rem   write_state %1=file %2=plugin name %3=cap1 %4=cap2 %5=version
rem ===========================================================================
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


rem ===========================================================================
rem   select_data_dir %1=explicit path or empty  -- prefers a nightly folder
rem ===========================================================================
:select_data_dir
set "TARGET_DATA_DIR="
if not "%~1"=="" (
    set "TARGET_DATA_DIR=%~1"
    goto ensure_data_dir
)
if defined ORCA_DATA_DIR (
    set "TARGET_DATA_DIR=%ORCA_DATA_DIR%"
    goto ensure_data_dir
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
    echo Found nightly data dir: "!NCAND_1!"
    goto ensure_data_dir
)
if "%NCOUNT%"=="0" if "%OCOUNT%"=="1" (
    set "TARGET_DATA_DIR=!OCAND_1!"
    echo Found data dir: "!OCAND_1!"
    goto ensure_data_dir
)
if "%NCOUNT%"=="0" if "%OCOUNT%"=="0" (
    set "TARGET_DATA_DIR=%APPDATA%\OrcaSlicer"
    echo No OrcaSlicer data dir found; defaulting to "%APPDATA%\OrcaSlicer".
    goto ensure_data_dir
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
for /L %%I in (1,1,%IDX%) do if "%DPICK%"=="%%I" set "TARGET_DATA_DIR=!MENU_%%I!"
if not defined TARGET_DATA_DIR set "TARGET_DATA_DIR=%DPICK%"

:ensure_data_dir
set "TARGET_DATA_DIR=%TARGET_DATA_DIR:"=%"
if exist "%TARGET_DATA_DIR%" exit /b 0
echo.
echo Orca data directory does not exist yet:
echo "%TARGET_DATA_DIR%"
set /p MAKE_DIR=Create it? [Y/N] 
if /I not "!MAKE_DIR!"=="Y" exit /b 1
mkdir "%TARGET_DATA_DIR%"
if errorlevel 1 exit /b 1
exit /b 0


:show_help
echo.
echo Install / update every OrcaSlicer plugin in this repo.
echo.
echo   Install-Orca-Plugins.bat                            everything, newest
echo   Install-Orca-Plugins.bat "C:\path\to\OrcaSlicer"    that data folder
echo   Install-Orca-Plugins.bat --local                    files beside this .bat
echo   Install-Orca-Plugins.bat --help                     this message
echo.
echo Environment overrides:
echo   ORCA_DATA_DIR   default Orca data directory
echo   PLUGIN_BRANCH   git branch/tag to pull from
echo   PLUGIN_ONLY     comma-separated ids, e.g. "wave-overhangs,unlayered-infill"
echo.
pause
exit /b 0

:fail
echo.
echo Install/update FAILED.
echo.
pause
exit /b 1

:done
pause
exit /b 0
