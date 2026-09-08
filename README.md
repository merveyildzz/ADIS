# AI-Powered Data Cleaning & Insight Platform

## Phase 0 — Project Setup & Guardrails (done)

### Layout
```
backend/   FastAPI app (config, logging, DB startup checks)
frontend/  UI (added in Phase 9)
data/synthetic/  generated datasets (Phase 1) + the SQLite DB file
db/migrations/   Alembic migrations (Phase 2)
tests/     pytest suite
```

### Running locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env   # fill in LLM_API_KEY if you have one; optional
cd backend && uvicorn app.main:app --reload
```
Health check: `GET http://127.0.0.1:8000/health`

### Tests
```bash
source .venv/bin/activate
pytest
```
