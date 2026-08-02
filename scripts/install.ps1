$ErrorActionPreference = "Stop"
python -m pip install --user --no-build-isolation $PSScriptRoot\..
Write-Host "Installed. Open a new terminal if 'sera' is not yet on PATH."
python -m sera --version
