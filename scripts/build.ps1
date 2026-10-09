$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path .venv)) { py -3.12 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) { throw 'Python dependency install failed' }
npm --prefix frontend ci
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency install failed' }
npm --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
if (Test-Path build) { Remove-Item -Recurse -Force build }
& .\.venv\Scripts\python.exe -m build --no-isolation
if ($LASTEXITCODE -ne 0) { throw 'Wheel build failed' }
