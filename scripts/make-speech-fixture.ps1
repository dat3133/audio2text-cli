param(
    [string]$Output = '.test-data\speech-test.wav',
    [ValidateSet('en','zh')][string]$Language = 'en',
    [string]$Text
)
$ErrorActionPreference = 'Stop'
$path = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($path)) -Force | Out-Null
$voice = New-Object -ComObject SAPI.SpVoice
$tokens = $voice.GetVoices()
$languageId = if ($Language -eq 'zh') { '804' } else { '409' }
$selected = $null
for ($index = 0; $index -lt $tokens.Count; $index++) {
    $token = $tokens.Item($index)
    if ($token.GetAttribute('Language') -eq $languageId) { $selected = $token; break }
}
if (-not $selected) { throw "No installed SAPI voice for $Language." }
$voice.Voice = $selected
$stream = New-Object -ComObject SAPI.SpFileStream
$stream.Format.Type = 22
$stream.Open($path, 3, $false)
$voice.AudioOutputStream = $stream
if (-not $Text) {
    $Text = if ($Language -eq 'zh') { '今天讨论语音转文字项目。初始化向导会检测显卡和内存，让用户选择适合电脑的模型。转写结果默认使用JSON格式，音频数据不会上传到网络。' }
        else { 'Hello. This is a local speech recognition test. The meeting starts at nine tomorrow morning. We will discuss the project requirements and the model selection.' }
}
[void]$voice.Speak($Text)
$stream.Close()
Write-Output $path
Write-Output $Text
