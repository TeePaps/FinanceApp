@echo off
rem Double-click installer for FinanceApp (Windows).
rem
rem Runs install.ps1 with -ExecutionPolicy Bypass: the copy next to this file if
rem there is one (local testing), otherwise the one published with the latest
rem release. Arguments and the FINANCEAPP_* environment overrides documented
rem in install.ps1 pass through.
rem
rem Not signed: if SmartScreen warns, click "More info" -> "Run anyway".
setlocal
cd /d "%USERPROFILE%"
echo ==============================================
echo  FinanceApp installer
echo ==============================================
echo.

set "FA_PS1=%~dp0install.ps1"
if exist "%FA_PS1%" goto run

set "FA_PS1=%TEMP%\financeapp-install-%RANDOM%%RANDOM%.ps1"
set "FA_PS1_URL=%FINANCEAPP_INSTALL_PS1_URL%"
if "%FA_PS1_URL%"=="" set "FA_PS1_URL=https://github.com/TeePaps/FinanceApp/releases/latest/download/install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri $env:FA_PS1_URL -OutFile $env:FA_PS1"
if errorlevel 1 (
  echo ERROR: could not download %FA_PS1_URL%
  set "FA_STATUS=1"
  goto done
)
set "FA_TEMP_PS1=1"

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%FA_PS1%" %*
set "FA_STATUS=%ERRORLEVEL%"
if defined FA_TEMP_PS1 del /q "%FA_PS1%" >nul 2>&1

:done
echo.
if "%FA_STATUS%"=="0" (
  echo Done. Open FinanceApp from the Desktop or Start Menu shortcut any time.
) else (
  echo Installation failed ^(exit %FA_STATUS%^). Scroll up for details.
)
if not defined FINANCEAPP_NO_PAUSE pause
exit /b %FA_STATUS%
