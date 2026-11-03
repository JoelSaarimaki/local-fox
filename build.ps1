# Builds the Local Fox executable into dist\LocalFox\LocalFox.exe, with the tools of ollmcp-generic-tools bundled.
# Creates the virtual environment .venv with the dependencies first if it does not exist, and installs the tools
# from -ToolsRepo (the sibling repository by default) into it on every build, so the executable has their current version.
param([string]$ToolsRepo = "..\ollmcp-generic-tools")
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit 1 }
    & .venv\Scripts\python.exe -m pip install --upgrade pip
    & .venv\Scripts\python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit 1 }
}
if (-not (Test-Path "$ToolsRepo\pyproject.toml")) { Write-Output "The tools repository was not found: $ToolsRepo"; exit 1 }
# Its dependencies first, then the package itself again, as its version number does not change with its code.
# Not editable: PyInstaller does not follow the import hooks of an editable install.
cmd /c ".venv\Scripts\python.exe -m pip install --quiet `"$ToolsRepo`" 2>&1"
if ($LASTEXITCODE -ne 0) { Write-Output "The tools were not installed."; exit 1 }
cmd /c ".venv\Scripts\python.exe -m pip install --quiet --no-deps --force-reinstall `"$ToolsRepo`" 2>&1"
if ($LASTEXITCODE -ne 0) { Write-Output "The tools were not installed."; exit 1 }
# The executable's icon, from the logo
cmd /c ".venv\Scripts\python.exe -m local_fox.ui.logo build\local_fox.ico 2>&1"
if ($LASTEXITCODE -ne 0) { Write-Output "The icon was not written."; exit 1 }
# PyInstaller logs to stderr; cmd runs it so that Windows PowerShell does not take its log lines for errors
cmd /c ".venv\Scripts\python.exe -m PyInstaller --noconfirm --log-level WARN --distpath dist --workpath build packaging\local_fox.spec 2>&1"
if ($LASTEXITCODE -ne 0) { Write-Output "The build failed."; exit 1 }
Write-Output "Built dist\LocalFox\LocalFox.exe"
