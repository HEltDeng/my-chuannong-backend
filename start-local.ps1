$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
$runtime = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (!(Test-Path -LiteralPath $runtime)) {
    $runtime = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
}
if (!(Test-Path -LiteralPath $runtime)) { throw '请先创建项目 Python 虚拟环境并安装 requirements.lock.txt。' }
& $runtime "$PSScriptRoot/run_local.py" @args
