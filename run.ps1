param(
    [switch]$Preview,
    [switch]$Open,
    [string]$Config = "config.json",
    [string]$FeedUrl = "",
    [string]$FeedName = "手动订阅"
)
$ErrorActionPreference = "Stop"
$taskRoot = $PSScriptRoot
$taskPython = Join-Path $taskRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $taskPython)) {
    & python -m venv (Join-Path $taskRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Python 虚拟环境创建失败" }
    & $taskPython -m pip install -r (Join-Path $taskRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
}
$taskArgs = @((Join-Path $taskRoot "daily.py"), "--config", $Config)
if ($Preview) { $taskArgs += "--preview" }
if ($FeedUrl) { $taskArgs += @("--feed-url", $FeedUrl, "--feed-name", $FeedName) }
& $taskPython @taskArgs
if ($LASTEXITCODE -ne 0) { throw "日报运行失败，退出码 $LASTEXITCODE" }
if ($Open) {
    $taskSettingsPath = if ([IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $taskRoot $Config }
    $taskSettings = Get-Content -LiteralPath $taskSettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $taskOutput = if ([IO.Path]::IsPathRooted($taskSettings.output_dir)) { $taskSettings.output_dir } else { Join-Path $taskRoot $taskSettings.output_dir }
    $taskResult = Get-Content -LiteralPath (Join-Path $taskOutput "last-run.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    Start-Process -FilePath $taskResult.html_path
}
