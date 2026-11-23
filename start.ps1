# MQTT Log Viewer launcher (UTF-8 with BOM)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$Port = 8000
$Url = "http://127.0.0.1:$Port"
$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$Requirements = Join-Path $PSScriptRoot "requirements.txt"
$InstalledMarker = Join-Path $PSScriptRoot ".venv\requirements.installed"

Write-Host "============================================"
Write-Host "  MQTT Log Viewer"
Write-Host "============================================"
Write-Host ""

function Test-PortInUse([int]$PortNumber) {
    try {
        $listeners = Get-NetTCPConnection -LocalPort $PortNumber -State Listen -ErrorAction SilentlyContinue
        return $null -ne $listeners
    } catch {
        $netstat = netstat -ano | Select-String ":$PortNumber\s+.*LISTENING"
        return $null -ne $netstat
    }
}

function Wait-And-OpenBrowser {
    for ($i = 0; $i -lt 60; $i++) {
        try {
            Invoke-WebRequest -UseBasicParsing "$Url/api/health" -TimeoutSec 1 | Out-Null
            Start-Process $Url
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
}

if (Test-PortInUse $Port) {
    Write-Host "ポート $Port はすでに使用中です。起動済みのビューアをブラウザで開きます。"
    Write-Host "別のアプリが $Port を使っている場合は、そのアプリを終了してから再実行してください。"
    Start-Process $Url
    Write-Host ""
    Read-Host "終了するには Enter キーを押してください"
    exit 0
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        Write-Host "Python が見つかりません。"
        Write-Host "https://www.python.org/downloads/ から Python 3.11 以上をインストールし、"
        Write-Host "インストール時に Add python.exe to PATH にチェックを入れてください。"
        Write-Host ""
        Read-Host "終了するには Enter キーを押してください"
        exit 1
    }
    Write-Host "[初回のみ] 実行環境を作成しています..."
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "セットアップに失敗しました。上に表示されたエラーを確認してください。"
        Read-Host "終了するには Enter キーを押してください"
        exit 1
    }
}

$needInstall = $true
if ((Test-Path -LiteralPath $InstalledMarker) -and (Test-Path -LiteralPath $Requirements)) {
    $needInstall = (Get-FileHash $Requirements).Hash -ne (Get-FileHash $InstalledMarker).Hash
}
if ($needInstall) {
    Write-Host "必要なライブラリをインストールしています（数分かかることがあります）..."
    & $VenvPython -m pip install --disable-pip-version-check -q -r $Requirements
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "セットアップに失敗しました。上に表示されたエラーを確認してください。"
        Write-Host "ネットワークにつながっているか、社内プロキシの制限がないかも確認してください。"
        Read-Host "終了するには Enter キーを押してください"
        exit 1
    }
    Copy-Item -LiteralPath $Requirements -Destination $InstalledMarker -Force
}

Write-Host "起動中です。準備ができるとブラウザが自動で開きます。"
Write-Host "終了するときは、このウィンドウを閉じるか Ctrl + C を押してください。"
Write-Host ""

$opener = Start-Job -ScriptBlock {
    param($TargetUrl)
    for ($i = 0; $i -lt 60; $i++) {
        try {
            Invoke-WebRequest -UseBasicParsing "$TargetUrl/api/health" -TimeoutSec 1 | Out-Null
            Start-Process $TargetUrl
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
} -ArgumentList $Url

try {
    & $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
} finally {
    Stop-Job $opener -ErrorAction SilentlyContinue
    Remove-Job $opener -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "ビューアを終了しました。"
Read-Host "終了するには Enter キーを押してください"
