# Builds the Windows desktop app.
#
#   powershell -ExecutionPolicy Bypass -File desktop\build.ps1
#
# Output:
#   dist\TGLibrary\TGLibrary.exe        the app (the whole folder is needed)
#   dist\TGLibrary-Windows.zip          zip of that folder, to hand to users
#   dist\TGLibrary-Setup.exe            installer (only if Inno Setup 6 is installed)
#
# Needs: Python 3.12+ (uses .venv if present). Node.js is only needed when
# frontend\dist is missing or -RebuildFrontend is passed.

param([switch]$RebuildFrontend)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# 1. Web UI
if ($RebuildFrontend -or -not (Test-Path "frontend\dist\index.html")) {
    Write-Host "== Building the web UI" -ForegroundColor Cyan
    Push-Location frontend
    npm ci --no-audit --no-fund; if ($LASTEXITCODE) { throw "npm ci failed" }
    npm run build; if ($LASTEXITCODE) { throw "npm run build failed" }
    Pop-Location
}

# 2. Python environment
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "== Creating .venv" -ForegroundColor Cyan
    py -3 -m venv .venv; if ($LASTEXITCODE) { python -m venv .venv }
}
Write-Host "== Installing Python packages" -ForegroundColor Cyan
& $Py -m pip install --quiet -r backend\requirements.txt pyinstaller
if ($LASTEXITCODE) { throw "pip install failed" }

# 3. Icon
if (-not (Test-Path "desktop\icon.ico")) { & $Py desktop\make_icon.py }

# 4. Freeze. Build in %TEMP% (much faster than on a network drive), then copy out.
Write-Host "== Building TGLibrary.exe" -ForegroundColor Cyan
$Work = Join-Path $env:TEMP "tglibrary-build"
& $Py -m PyInstaller desktop\tglibrary.spec --noconfirm --clean `
    --workpath "$Work\build" --distpath "$Work\dist"
if ($LASTEXITCODE) { throw "PyInstaller failed" }

New-Item -ItemType Directory -Force dist | Out-Null

# 5. Zip for easy sharing (made from the fresh build, so it never depends on dist\TGLibrary)
$Zip = "dist\TGLibrary-Windows.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Compress-Archive -Path "$Work\dist\TGLibrary" -DestinationPath $Zip

# Unzipped copy. Fails if TGLibrary.exe from that folder is running or the
# folder is open in Explorer; the zip above is still up to date then.
try {
    if (Test-Path dist\TGLibrary) { Remove-Item -Recurse -Force dist\TGLibrary -ErrorAction Stop }
    Copy-Item -Recurse "$Work\dist\TGLibrary" dist\TGLibrary -ErrorAction Stop
} catch {
    Write-Host "Could not refresh dist\TGLibrary (close the app / Explorer windows using it): $_" -ForegroundColor Yellow
    Write-Host "Fresh build is in $Work\dist\TGLibrary and in $Zip" -ForegroundColor Yellow
}

# 6. Installer (optional)
$Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($Iscc) {
    Write-Host "== Building installer" -ForegroundColor Cyan
    & $Iscc /Q "/DSourceDir=$Work\dist\TGLibrary" desktop\installer.iss
    if ($LASTEXITCODE) { throw "Inno Setup failed" }
} else {
    Write-Host "Inno Setup 6 not found; skipping the installer (the zip works without it)." -ForegroundColor Yellow
    Write-Host "Install it with: winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
}

Write-Host "`nDone:" -ForegroundColor Green
Get-ChildItem dist -File | ForEach-Object { Write-Host "  $($_.FullName)  ($([math]::Round($_.Length/1MB,1)) MB)" }
Write-Host "  $Root\dist\TGLibrary\TGLibrary.exe"
