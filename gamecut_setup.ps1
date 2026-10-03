# gamecut の起動スクリプト（Windows）
# Python・仮想環境・ライブラリがなければ自動で用意してから gamecut を実行する。
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$HomeDir = Join-Path $env:USERPROFILE ".gamecut"
$Venv = Join-Path $HomeDir "venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$PyVersion = "3.12.7"

function Say($msg) { Write-Host "[準備] $msg" }

function Test-Python($exe, [string[]]$pre) {
    try {
        & $exe @pre -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-Python {
    foreach ($v in @("3.13", "3.12", "3.11", "3.10")) {
        if ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Python "py" @("-$v"))) { return @("py", "-$v") }
    }
    foreach ($v in @("313", "312", "311", "310")) {
        $p = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe"
        if ((Test-Path $p) -and (Test-Python $p @())) { return @($p) }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and ($cmd.Source -notlike "*WindowsApps*") -and (Test-Python $cmd.Source @())) { return @($cmd.Source) }
    return $null
}

function Install-Python {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "Python を winget で入れます"
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) { return }
    }
    Say "Python $PyVersion を python.org から入れます"
    $exe = Join-Path $env:TEMP "python-installer.exe"
    Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-amd64.exe" -OutFile $exe
    Start-Process -Wait -FilePath $exe -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1"
}

if (-not (Test-Path $VenvPy)) {
    $py = Find-Python
    if (-not $py) {
        Install-Python
        $py = Find-Python
        if (-not $py) { Write-Host "Python を見つけられませんでした。いったん閉じてもう一度実行してください。"; exit 1 }
    }
    Say "専用の環境を作成します ($Venv)"
    New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
    $exe = $py[0]
    $pre = @()
    if ($py.Count -gt 1) { $pre = $py[1..($py.Count - 1)] }
    & $exe @pre -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Write-Host "環境の作成に失敗しました。"; exit 1 }
}

$Req = Join-Path $Dir "requirements.txt"
$HashFile = Join-Path $Venv ".requirements.sha"
$NewHash = (Get-FileHash $Req -Algorithm SHA1).Hash
$OldHash = ""
if (Test-Path $HashFile) { $OldHash = (Get-Content $HashFile -Raw).Trim() }
if ($OldHash -ne $NewHash) {
    Say "必要なライブラリを入れます（初回は数分かかります）"
    & $VenvPy -m pip install --disable-pip-version-check -q --upgrade pip
    & $VenvPy -m pip install --disable-pip-version-check -r $Req
    if ($LASTEXITCODE -ne 0) { Write-Host "ライブラリのインストールに失敗しました。ネット接続を確認してください。"; exit 1 }
    Set-Content -Path $HashFile -Value $NewHash
}

$env:PYTHONPATH = $Dir
& $VenvPy -m gamecut @args
exit $LASTEXITCODE
