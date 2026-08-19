param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsList
)

$pythonExe = Join-Path $PSScriptRoot "crawler\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Error "Virtual environment not found at $pythonExe. Please setup crawler venv first."
    exit 1
}

& $pythonExe -m crawler @ArgsList
