param([string]$InnoCompiler = '')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Create .venv and install requirements-desktop.txt first.' }
& $python -m PyInstaller --noconfirm OfferHelper.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
if (!$InnoCompiler) {
    $candidate = Join-Path $PSScriptRoot 'build-tools\inno\ISCC.exe'
    if (Test-Path -LiteralPath $candidate) { $InnoCompiler = $candidate }
    elseif (Get-Command ISCC.exe -ErrorAction SilentlyContinue) { $InnoCompiler = (Get-Command ISCC.exe).Source }
}
if ($InnoCompiler) {
    & $InnoCompiler installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
} else {
    & $python packaging/build_setup.py
    if ($LASTEXITCODE -ne 0) { throw 'Windows installer build failed.' }
}
