$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\..\..\..\apps\backend-api"
python -m scripts.migrate_db
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
