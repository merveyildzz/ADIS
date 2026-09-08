# AI-Powered Data Cleaning & Insight Platform

## Phase 0 — Project Setup & Guardrails (done)

### Layout
```
backend/   FastAPI app (config, logging, DB models/repository, synthetic generator)
frontend/  UI (added in Phase 9)
data/      the SQLite DB file, plus data/synthetic/ generated datasets (Phase 1)
db/        Alembic migrations (env.py, versions/) — alembic.ini lives at repo root
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

## Phase 2 — Database Design & SQL Safety Layer (done)

Four tables (`raw_uploads`, `cleaned_records`, `feedback_corrections`, `audit_log`)
via SQLAlchemy ORM models (`backend/app/db/models.py`), with FKs, a confidence-score
CHECK constraint, and the indexes `cleaned_records` needs for its two hot query
patterns (by `upload_id`+`column_name`, and by `upload_id`+`confidence_score`).
All reads/writes go through `backend/app/db/repository.py` — parameterized ORM
calls only, never a hand-built SQL string.

```bash
source .venv/bin/activate
alembic upgrade head      # applies db/versions/*.py to DATABASE_URL
alembic downgrade base    # reverts, if you need to start clean
```

## Phase 3 — Orchestrator & Column Type Detection (done)

`backend/app/orchestrator/` validates an uploaded file (`file_validation.py`) and
then routes each column to a specialist agent by sampling its values —
column-name heuristics + regex, no LLM (`column_detection.py`). A column's
header name alone can never force a routing decision: value evidence must
independently clear its own threshold first, so e.g. a column named
`order_date_notes` full of free text stays `unclassified` instead of going
to the Date Agent. Every column decision is written to the Phase 0 audit
trail (agent name `Orchestrator`).

File validation covers every adversarial case from the roadmap: empty file,
header-only file, duplicate column names (warned, not silently overwritten),
oversized file (rejected before parsing), a binary file renamed to `.csv`
(magic-byte sniff), and wrong text encoding (UTF-8 attempted first, falls
back to `chardet` detection rather than crashing).

What's enforced, and proven with tests (not just written):
- SQL-injection payloads in any field are stored as inert text — proven by
  inserting `Robert'); DROP TABLE customers;--` and confirming the table survives
- A cleaning run's inserts are one transaction: a bad row rolls back the whole batch
- Foreign keys are enforced (SQLite has them off by default — turned on via PRAGMA)
- SQLite runs in WAL mode with a busy-timeout, so concurrent writes from different
  uploads don't corrupt state or deadlock (tested with 8 concurrent writers)
- `cleaned_records`/`audit_log` reads are always paginated with a hard server-side cap
- A corrupted or malformed database fails startup with a clean message, not a crash
