param(
    [string]$Repository = 'dat3133/audio2text-cli',
    [string]$Version = '0.1.0-beta.3',
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
$partial = Join-Path $targetDir ('audio2text.' + [guid]::NewGuid().ToString('N') + '.partial.exe')
if ($ArtifactPath) {
    if (-not $ChecksumPath) { throw 'Local artifacts require -ChecksumPath.' }
    $checksums = Get-Content -LiteralPath $ChecksumPath -Raw
    Copy-Item -LiteralPath $ArtifactPath -Destination $partial
} else {
    if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Invalid -Repository; expected owner/repository.' }
    $base = "https://github.com/$Repository/releases/download/v$Version"
    $manifestPath = $partial + '.SHA256SUMS'
    Invoke-WebRequest -Uri "$base/SHA256SUMS" -OutFile $manifestPath
    $checksums = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8
    Invoke-WebRequest -Uri "$base/$asset" -OutFile $partial
}
$entries = @($checksums -split '\r?\n' | Where-Object { $_ -match ('^[a-fA-F0-9]{64}\s+\*?' + [regex]::Escape($asset) + '$') })
if ($entries.Count -ne 1) { throw 'SHA256 manifest must have exactly one matching Windows asset.' }
$entry = $entries[0]
$expected = ($entry -split '\s+')[0]
if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $expected) { throw 'SHA256 mismatch; partial file retained for inspection.' }
$candidateVersion = & $partial --version
if ($LASTEXITCODE -ne 0 -or $candidateVersion -ne "audio2text $Version") { throw 'Downloaded binary version mismatch; previous installation retained.' }
$binary = Join-Path $targetDir 'audio2text.exe'
Move-Item -LiteralPath $partial -Destination $binary -Force
if (-not $NoPathUpdate) {
    if (($env:PATH -split ';') -notcontains $targetDir) { $env:PATH = "$targetDir;$env:PATH" }
    $userPath = [Environment]::GetEnvironmentVariable('Path','User')
    if (($userPath -split ';') -notcontains $targetDir) { [Environment]::SetEnvironmentVariable('Path',($targetDir + ';' + $userPath),'User') }
}
$installedVersion = & $binary --version
if ($LASTEXITCODE -ne 0 -or $installedVersion -ne "audio2text $Version") { throw 'Installed binary failed its version check.' }
Write-Output $installedVersion
Write-Host "CLI 安装成功：$binary"
if (-not $NoInit -and -not [Console]::IsInputRedirected -and -not [Console]::IsErrorRedirected) {
    & $binary init
    if ($LASTEXITCODE -ne 0) { Write-Warning 'CLI 已安装，初始化未完成。运行 audio2text init 重试。'; exit $LASTEXITCODE }
} else { Write-Host '运行 audio2text init 开始配置；自动化使用 init --non-interactive。' }
