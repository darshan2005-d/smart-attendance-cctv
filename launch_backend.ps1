# Smart Attendance AI PowerShell Launcher
$projectPath = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $projectPath) { $projectPath = "C:\Users\dharshan\OneDrive\Documents\Desktop\SMART ATTENDANCE PROJECT" }

Set-Location $projectPath

$pythonExe = Join-Path $projectPath "venv\Scripts\python.exe"
$backendScript = Join-Path $projectPath "backend.py"

if (-not (Test-Path $pythonExe)) {
    Write-Error "Python executable not found at: $pythonExe"
    exit 1
}

# Check if backend is already running
$existing = Get-Process -Name python, pythonw -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*$backendScript*"
}

if ($existing) {
    Write-Host "Smart Attendance server is already running. Opening dashboard..." -ForegroundColor Green
    Start-Process "http://127.0.0.1:5000"
    exit 0
}

Write-Host "Starting Smart Attendance Server..." -ForegroundColor Cyan
Start-Process -FilePath $pythonExe -ArgumentList "`"$backendScript`"" -WorkingDirectory $projectPath -WindowStyle Hidden

$maxWait = 25
$count = 0
$uri = 'http://127.0.0.1:5000/health'
while ($count -lt $maxWait) {
    try {
        $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Host "Server is online! Opening web interface..." -ForegroundColor Green
            Start-Process "http://127.0.0.1:5000"
            exit 0
        }
    } catch {
        Start-Sleep -Seconds 1
        $count++
    }
}

Write-Warning "Backend took longer than $maxWait seconds to respond. Opening browser anyway."
Start-Process "http://127.0.0.1:5000"
