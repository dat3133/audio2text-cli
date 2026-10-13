param([Parameter(Mandatory)][string]$Binary)
$ErrorActionPreference = 'Stop'
$audio2textRoot = Split-Path $PSScriptRoot
$audio2textFixtureBinary = [IO.Path]::GetFullPath($Binary)
$audio2textExpectedHash = (Get-FileHash -LiteralPath $audio2textFixtureBinary -Algorithm SHA256).Hash
$audio2textVersion = ((& $audio2textFixtureBinary --version) -split ' ')[1]
$audio2textTestDir = Join-Path $audio2textRoot ('.test-data\HTTP 清单 空格-' + [guid]::NewGuid().ToString('N'))
function Invoke-WebRequest {
    param([string]$Uri,[string]$OutFile)
    if ($Uri.EndsWith('/SHA256SUMS')) {
        $bytes = [Text.Encoding]::UTF8.GetBytes($audio2textExpectedHash + "  audio2text-windows-x86_64.exe`n")
    } elseif ($Uri.EndsWith('/audio2text-windows-x86_64.exe')) {
        $bytes = [IO.File]::ReadAllBytes($audio2textFixtureBinary)
    } else { throw 'Unexpected download URL' }
    if ($OutFile) { [IO.File]::WriteAllBytes($OutFile,$bytes) }
    else { [pscustomobject]@{Content=$bytes} }
}
& (Join-Path $audio2textRoot 'install.ps1') -InstallDir $audio2textTestDir -Version $audio2textVersion -NoInit -NoPathUpdate
if ($LASTEXITCODE -ne 0) { throw 'Mock HTTP installation failed' }
if ((Get-FileHash -LiteralPath (Join-Path $audio2textTestDir 'audio2text.exe')).Hash -ne $audio2textExpectedHash) { throw 'Installed bytes differ' }
Write-Output 'Binary HTTP manifest regression passed.'
