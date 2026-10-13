param(
    [string]$Repository = 'dat3133/audio2text-cli',
    [string]$Version = '0.1.0',
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'audio2text\bin'),
    [switch]$NoInit,
    [switch]$NoPathUpdate,
    [string]$ArtifactPath,
    [string]$ChecksumPath
)
$ErrorActionPreference = 'Stop'
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -notin @('AMD64','x86')) { throw 'Only Windows x86_64 is supported.' }
if ($Version -notmatch '^\d+\.\d+\.\d+(-[A-Za-z0-9.-]+)?$') { throw 'Invalid version.' }
$asset = 'audio2text-windows-x86_64.exe'
$targetDir = [IO.Path]::GetFullPath($InstallDir)
New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
$partial = Join-Path $targetDir ('audio2text.' + [guid]::NewGuid().ToString('N') + '.partial')
if ($ArtifactPath) {
    if (-not $ChecksumPath) { throw 'Local artifacts require -ChecksumPath.' }
    $checksums = Get-Content -LiteralPath $ChecksumPath -Raw
    Copy-Item -LiteralPath $ArtifactPath -Destination $partial
} else {
    if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Invalid -Repository; expected owner/repository.' }
    $base = "https://github.com/$Repository/releases/download/v$Version"
    $checksums = (Invoke-WebRequest -Uri "$base/SHA256SUMS").Content
    Invoke-WebRequest -Uri "$base/$asset" -OutFile $partial
}
$entry = ($checksums -split '\r?\n' | Where-Object { $_ -match ('^[a-fA-F0-9]{64}\s+\*?' + [regex]::Escape($asset) + '$') } | Select-Object -First 1)
if (-not $entry) { throw 'SHA256 manifest has no matching Windows asset.' }
$expected = ($entry -split '\s+')[0]
if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $expected) { throw 'SHA256 mismatch; partial file retained for inspection.' }
$binary = Join-Path $targetDir 'audio2text.exe'
Move-Item -LiteralPath $partial -Destination $binary -Force
if (-not $NoPathUpdate) {
    if (($env:PATH -split ';') -notcontains $targetDir) { $env:PATH = "$targetDir;$env:PATH" }
    $userPath = [Environment]::GetEnvironmentVariable('Path','User')
    if (($userPath -split ';') -notcontains $targetDir) { [Environment]::SetEnvironmentVariable('Path',($targetDir + ';' + $userPath),'User') }
}
& $binary --version
if ($LASTEXITCODE -ne 0) { throw 'Installed binary failed its version check.' }
Write-Host "CLI 安装成功：$binary"
if (-not $NoInit -and -not [Console]::IsInputRedirected -and -not [Console]::IsErrorRedirected) {
    & $binary init
    if ($LASTEXITCODE -ne 0) { Write-Warning 'CLI 已安装，初始化未完成。运行 audio2text init 重试。'; exit $LASTEXITCODE }
} else { Write-Host '运行 audio2text init 开始配置；自动化使用 init --non-interactive。' }
