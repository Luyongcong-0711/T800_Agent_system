# Agent System Backend

Phase A FastAPI backend skeleton.

## Run locally

```powershell
conda activate py313
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Verify

```powershell
conda activate py313
python -m pytest
```
