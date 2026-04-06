$ErrorActionPreference = "Stop"

$databaseFiles = @(
    ".\trading_system.db",
    ".\trading_system.db-wal",
    ".\trading_system.db-shm"
)

Write-Host "Resetting trading system without touching candle history..."

Write-Host "Stopping Python processes..."
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force

Write-Host "Removing __pycache__ folders..."
Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Write-Host "Removing .pyc files..."
Get-ChildItem -Recurse -File -Filter "*.pyc" | Remove-Item -Force

Write-Host "Protecting database files..."
foreach ($dbFile in $databaseFiles) {
    if (Test-Path $dbFile) {
        Write-Host "SAFE  $dbFile"
    } else {
        Write-Host "MISSING $dbFile"
    }
}

Write-Host "Reset complete. Database files were preserved."
