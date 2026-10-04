# FinanceApp installer bootstrap (Windows PowerShell 5.1+).
#
#   irm https://github.com/TeePaps/FinanceApp/releases/latest/download/install.ps1 | iex
#   powershell -ExecutionPolicy Bypass -File install.ps1 --port 9000
#
# Ensures uv (official installer, PATH left untouched), downloads installer.py
# from the latest release, and runs it with uv-managed Python 3.12. Arguments
# (when run with -File) are passed to installer.py; with "irm | iex" use
# $env:FINANCEAPP_INSTALL_ARGS (e.g. "--port 9000") instead.
#
# Testing / offline overrides (environment):
#   FINANCEAPP_INSTALLER_PY   local installer.py to run instead of downloading
#   FINANCEAPP_INSTALLER_URL  URL to download installer.py from
#   FINANCEAPP_INSTALLER_ZIP  local release zip (read by installer.py as --zip)
#   FINANCEAPP_UPDATE_FEED    release feed JSON (read by installer.py as --feed-url)
#   FINANCEAPP_UV             uv.exe to use
#
# Written as a function that returns (never "exit"), so "irm | iex" does not
# close the user's PowerShell window. Its output is not captured (the exit code
# goes to $script:FinanceAppExit) so installer.py talks to the console directly.

$FinanceAppArgs = @($args)

function Install-FinanceApp {
    param([string[]]$PassArgs)

    $ErrorActionPreference = 'Stop'
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch { }

    $repo = 'TeePaps/FinanceApp'
    $installerUrl = $env:FINANCEAPP_INSTALLER_URL
    if (-not $installerUrl) { $installerUrl = "https://github.com/$repo/releases/latest/download/installer.py" }

    function Find-Uv {
        if ($env:FINANCEAPP_UV -and (Test-Path -LiteralPath $env:FINANCEAPP_UV)) { return $env:FINANCEAPP_UV }
        $cmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        $cands = @()
        if ($env:UV_INSTALL_DIR) { $cands += (Join-Path $env:UV_INSTALL_DIR 'uv.exe') }
        $cands += (Join-Path $env:USERPROFILE '.local\bin\uv.exe')
        $cands += (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe')
        foreach ($c in $cands) { if (Test-Path -LiteralPath $c) { return $c } }
        return $null
    }

    $uv = Find-Uv
    if (-not $uv) {
        Write-Host 'Installing uv (Python package manager) from astral.sh ...'
        $env:UV_NO_MODIFY_PATH = '1'
        & powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        $uv = Find-Uv
        if (-not $uv) { Write-Host 'ERROR: uv installation failed (expected %USERPROFILE%\.local\bin\uv.exe)' -ForegroundColor Red; $script:FinanceAppExit = 1; return }
    }
    $env:FINANCEAPP_UV = $uv
    Write-Host "Using uv: $uv"

    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('financeapp-bootstrap-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        if ($env:FINANCEAPP_INSTALLER_PY) {
            if (-not (Test-Path -LiteralPath $env:FINANCEAPP_INSTALLER_PY)) {
                Write-Host "ERROR: FINANCEAPP_INSTALLER_PY not found: $($env:FINANCEAPP_INSTALLER_PY)" -ForegroundColor Red
                $script:FinanceAppExit = 1; return
            }
            $installer = $env:FINANCEAPP_INSTALLER_PY
        } else {
            $installer = Join-Path $tmp 'installer.py'
            Write-Host 'Downloading installer ...'
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest -UseBasicParsing -Uri $installerUrl -OutFile $installer
        }

        $all = @()
        if ($PassArgs) { $all += $PassArgs }
        if ($env:FINANCEAPP_INSTALL_ARGS) { $all += ($env:FINANCEAPP_INSTALL_ARGS -split '\s+' | Where-Object { $_ }) }

        # Only uv-managed Pythons: never use or modify the system Python.
        $env:UV_PYTHON_PREFERENCE = 'only-managed'
        # Not captured, so the native process talks to the console directly
        # (prompts without a newline still show up).
        & $uv run --no-project --python 3.12 $installer @all
        $script:FinanceAppExit = $LASTEXITCODE
    } catch {
        Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
        $script:FinanceAppExit = 1; return
    } finally {
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$FinanceAppExit = 0
Install-FinanceApp -PassArgs $FinanceAppArgs
# Running as a script file (-File): propagate the exit code. Under "irm | iex"
# $PSCommandPath is empty and we must not exit the user's shell.
if ($PSCommandPath) { exit $FinanceAppExit }
