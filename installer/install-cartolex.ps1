# SPDX-License-Identifier: MIT
# Install or update cartolex for this user on Windows, without administrator rights.
# Started by "Install cartolex.bat", next to this file.
#
# Everything goes into one folder, %USERPROFILE%\cartolex (CARTOLEX_HOME changes it):
# the Python environment, uv and the Python it downloads, their caches and install.log.
# The route: uv (installed into that folder unless one is on the PATH), then uv trusting
# the system's certificates (UV_NATIVE_TLS, for networks that inspect HTTPS), then the
# Python 3.10 or later installed on this computer. HTTPS_PROXY, HTTP_PROXY and NO_PROXY
# are followed.
#
# For testing: CARTOLEX_WHEEL=C:\path\cartolex-*.whl (or a wheel next to this file)
# installs that file instead of the pinned release; CARTOLEX_ROUTE=system skips uv.

$ErrorActionPreference = 'Continue'
$Pin = '@CARTOLEX_VERSION@'
$Models = @('en', 'fr', 'pt')
$UvPythonVersion = '3.12'

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = if ($env:CARTOLEX_HOME) { $env:CARTOLEX_HOME } else { Join-Path $env:USERPROFILE 'cartolex' }
$EnvDir = Join-Path $Root 'env'
$EnvPython = Join-Path $EnvDir 'Scripts\python.exe'
$Cartolex = Join-Path $EnvDir 'Scripts\cartolex.exe'
$Log = Join-Path $Root 'install.log'

New-Item -ItemType Directory -Force -Path $Root | Out-Null

function Say([string]$Text) {
    Write-Host $Text
    Add-Content -Path $Log -Value $Text -Encoding UTF8
}

function Step([string]$Text) { Say ''; Say "== $Text" }

function Stop-Install([string]$Text) {
    Say ''
    Say "The installation stopped: $Text"
    Say "The whole log is in $Log"
    exit 1
}

# Runs a program, shows and logs its output; returns its exit code.
function Invoke-Logged([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments 2>&1 | ForEach-Object {
        $line = "$_"
        Write-Host $line
        Add-Content -Path $Log -Value $line -Encoding UTF8
    }
    return $LASTEXITCODE
}

function Test-Env {
    if (-not (Test-Path $EnvPython)) { return $false }
    & $EnvPython -c 'import sys' 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

Say ''
Say ("==== cartolex installer, {0}, Windows {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), [Environment]::OSVersion.Version)
Say "folder: $Root"
foreach ($name in 'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY') {
    if ([Environment]::GetEnvironmentVariable($name)) { Say "proxy setting in use: $name" }
}

# What to install: the pinned release, or a wheel given for testing.
$Wheel = $env:CARTOLEX_WHEEL
if (-not $Wheel) {
    $found = Get-ChildItem -Path $Here -Filter 'cartolex-*.whl' -ErrorAction SilentlyContinue | Select-Object -Last 1
    if ($found) { $Wheel = $found.FullName }
}
if ($Wheel) {
    if (-not (Test-Path $Wheel)) { Stop-Install "the wheel $Wheel does not exist" }
    $Spec = $Wheel
    Say "installing the wheel $Wheel"
} elseif ($Pin.StartsWith('@')) {
    Stop-Install 'this copy of the installer names no version: use the kit of a release'
} else {
    $Spec = "cartolex==$Pin"
    Say "installing cartolex $Pin"
}

$env:UV_CACHE_DIR = Join-Path $Root 'cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root 'python'
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:UV_NO_MODIFY_PATH = '1'
$env:PIP_CACHE_DIR = Join-Path $Root 'cache\pip'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PYTHONUTF8 = '1'

# --- uv ---

function Get-Uv {
    $own = Join-Path $Root 'uv\uv.exe'
    if (Test-Path $own) { return $own }
    $onPath = Get-Command uv -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    Say 'downloading uv from astral.sh'
    $env:UV_INSTALL_DIR = Join-Path $Root 'uv'
    $command = '[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; ' +
        'irm https://astral.sh/uv/install.ps1 | iex'
    $code = Invoke-Logged 'powershell.exe' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $command)
    if ($code -ne 0 -or -not (Test-Path $own)) {
        Say 'astral.sh cannot be reached, or the uv installer failed'
        return $null
    }
    return $own
}

function Install-WithUv([string]$Uv) {
    if (-not (Test-Env)) {
        if (Test-Path $EnvDir) { Remove-Item -Recurse -Force $EnvDir }
        if ((Invoke-Logged $Uv @('venv', '--quiet', '--python', $UvPythonVersion, $EnvDir)) -ne 0) { return $false }
    }
    if ((Invoke-Logged $Uv @('pip', 'install', '--python', $EnvPython, '--upgrade', $Spec)) -ne 0) { return $false }
    # cartolex models add installs with uv when the environment has no pip.
    $saved = $env:PATH
    $env:PATH = (Split-Path -Parent $Uv) + ';' + $env:PATH
    $code = Invoke-Logged $Cartolex (@('models', 'add') + $Models + @('--yes'))
    $env:PATH = $saved
    return ($code -eq 0)
}

# --- the Python installed on this computer ---

function Find-Python {
    $probe = 'import sys; sys.exit(sys.version_info < (3, 10))'
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & py -3 -c $probe 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return (& py -3 -c 'import sys; print(sys.executable)').Trim() }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike '*WindowsApps*') {
        & $python.Source -c $probe 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $python.Source }
    }
    return $null
}

function Install-WithPython {
    if (-not (Test-Env)) {
        $python = Find-Python
        if (-not $python) {
            Stop-Install ('no Python 3.10 or later on this computer, and uv could not be used. ' +
                'Install Python from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), ' +
                'then run this installer again.')
        }
        Say "Python: $python"
        if (Test-Path $EnvDir) { Remove-Item -Recurse -Force $EnvDir }
        if ((Invoke-Logged $python @('-m', 'venv', $EnvDir)) -ne 0) { Stop-Install 'Python could not create an environment' }
    }
    & $EnvPython -m pip --version 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if ((Invoke-Logged $EnvPython @('-m', 'ensurepip', '--upgrade')) -ne 0) { return $false }
    }
    if ((Invoke-Logged $EnvPython @('-m', 'pip', 'install', '--upgrade', $Spec)) -ne 0) { return $false }
    return ((Invoke-Logged $Cartolex (@('models', 'add') + $Models + @('--yes'))) -eq 0)
}

# --- the route ---

$Route = $null
if ($env:CARTOLEX_ROUTE -eq 'system') {
    Say "route: the Python of this computer (asked by CARTOLEX_ROUTE)"
} else {
    $uv = Get-Uv
    if ($uv) {
        Say "uv: $uv"
        Step 'installing with uv'
        if (Install-WithUv $uv) {
            $Route = 'uv'
        } else {
            Step "uv failed; again with the system's certificates (UV_NATIVE_TLS)"
            $env:UV_NATIVE_TLS = '1'
            if (Install-WithUv $uv) {
                $Route = 'uv with native TLS'
            } else {
                Remove-Item Env:UV_NATIVE_TLS
                Say "uv failed with the system's certificates too"
            }
        }
    } else {
        Say 'uv cannot be used'
    }
}
if (-not $Route) {
    Step 'installing with the Python of this computer'
    if (-not (Install-WithPython)) { Stop-Install 'pip could not install cartolex (see the messages above)' }
    $Route = 'system Python'
}
Say "route taken: $Route"

& $Cartolex --help 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Stop-Install 'cartolex does not start' }
$version = (& $EnvPython -c "import importlib.metadata as m; print(m.version('cartolex'))").Trim()
Say "cartolex $version is installed in $EnvDir"

# --- the shortcuts ---

Step 'creating the shortcuts'
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    if (-not $folder) { continue }
    $link = $shell.CreateShortcut((Join-Path $folder 'cartolex.lnk'))
    $link.TargetPath = $Cartolex
    $link.Arguments = 'app'
    $link.WorkingDirectory = $env:USERPROFILE
    $link.Description = 'Map a research field from the texts of its people'
    $link.Save()
    Say "shortcut: $(Join-Path $folder 'cartolex.lnk')"
}

Say ''
Say "Done. Open cartolex with the shortcut on the desktop or in the Start menu."
Say "The log is in $Log"
exit 0
