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

## Phase 1 — Synthetic Dataset Generator (done)

Generates a deterministic "dirty" customer/order dataset with mixed date/currency
formats, malformed contacts, inconsistent addresses, SQL-injection payloads, and
embedded statistical relationships (age/amount correlation, a seasonal spike,
a counter-intuitive segment pattern, extreme outliers) for later phases to find.

```bash
source .venv/bin/activate
cd backend && python -m app.synthetic.generator --seed 42 --customers 500
```

Output goes to `data/synthetic/`:
- `dirty_dataset.csv` — the messy dataset to upload through the app
- `ground_truth.csv` — true pre-corruption values, row-aligned by `row_index`
- `manifest.json` — exact row indices of every injected defect/relationship
- `data_dictionary.md` — human-readable writeup of what was broken and why
