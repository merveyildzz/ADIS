# ADIS — Agentic Data Insight System

Upload a messy CSV, watch a multi-agent pipeline clean it with per-cell confidence scores, correct it and have the system learn from that correction, define your own validation rules, and get AI-generated insights (correlations, trends, anomalies) with charts — all backed by a proper SQL schema, not ad-hoc files.

For the full technical write-up (architecture, every agent, the test suite, real bugs found and fixed, security measures) see **[TECHNICAL_REPORT.md](TECHNICAL_REPORT.md)**.

---

## Requirements

- **Python 3.11+**
- **Node.js 18+** and npm
- **git** (or just download the code as a ZIP — see below)
- An **Anthropic** or **Google Gemini** API key — **optional**. Without one, the app still runs end-to-end: every LLM-backed feature (address resolution, AI column classification, insight narratives) automatically falls back to a non-LLM path. You only need a key if you want the LLM-assisted parts active.

---

## 1) Get the code onto your machine

**Option A — git clone:**
```bash
git clone https://github.com/merveyildzz/ADIS.git
cd ADIS
```

**Option B — download as ZIP:** on the GitHub page, "Code" → "Download ZIP", then unzip it and `cd` into the folder.

---

## 2) Backend setup

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
```

### Configure environment variables

```bash
cp backend/.env.example backend/.env
```

Open `backend/.env` and, at minimum, decide about the LLM key (see below). Everything else has a sane default:

```ini
# DATABASE_URL=sqlite:////absolute/path/to/app.db   # optional, defaults to data/app.db

LLM_API_KEY=                # see "Getting an API key" below — optional
LLM_PROVIDER=anthropic      # or: gemini
# LLM_MODEL=                # optional override, e.g. gemini-flash-latest

MAX_UPLOAD_SIZE_MB=20
CONFIDENCE_HIGH_THRESHOLD=90
CONFIDENCE_MEDIUM_THRESHOLD=60
LOG_LEVEL=INFO
ENVIRONMENT=development
```

### Getting an API key (optional, but here's how)

You need **one** of these, not both:

- **Anthropic (Claude)** — create a key at [console.anthropic.com](https://console.anthropic.com) → API Keys. Set:
  ```ini
  LLM_PROVIDER=anthropic
  LLM_API_KEY=sk-ant-...
  ```
- **Google Gemini** — create a free key at [aistudio.google.com](https://aistudio.google.com) → Get API key. Set:
  ```ini
  LLM_PROVIDER=gemini
  LLM_API_KEY=AI...
  LLM_MODEL=gemini-flash-latest
  ```
  ⚠️ **Gemini's free tier has a hard daily quota (as low as 20 requests/day per model)** — during heavy testing this runs out fast. When it does, the app doesn't break: LLM-backed features (address resolution for non-lookup-table cases, AI column classification, insight narratives) silently fall back to their deterministic/template behavior. You'll notice it as "Template (rule-based)" instead of "AI-phrased" in the insight cards, and previously-unclassifiable columns landing in "Profiled but not transformed" instead of getting an AI guess. This is expected, not a bug.
- **No key at all** works too — the whole pipeline (cleaning, rules, insights, charts) runs on its deterministic fallbacks. You'll see a startup log line noting LLM-backed agents are in fallback mode.

### Apply database migrations

```bash
alembic upgrade head
```
This creates `data/app.db` (SQLite) with the full schema. To start over cleanly: `alembic downgrade base` then `alembic upgrade head` again.

### (Optional) Generate a synthetic test dataset

```bash
cd backend && python -m app.synthetic.generator --seed 42 --customers 500 && cd ..
```
Produces `data/synthetic/dirty_dataset.csv` — a deterministic messy dataset good for trying the app immediately without hunting for your own CSV.

### Run the backend

```bash
cd backend && uvicorn app.main:app --reload
```
Health check: `http://127.0.0.1:8000/health` · API docs: `http://127.0.0.1:8000/docs`

---

## 3) Frontend setup

In a **second terminal**, from the repo root:

```bash
cd frontend
npm install
npm run dev
```

Open **`http://localhost:5173`**. The frontend talks to the backend at `http://localhost:8000` by default — if you're running the backend somewhere else, copy `frontend/.env.example` to `frontend/.env.local` and set `VITE_API_BASE`.

---

## 4) Try it

1. Drag a CSV onto the upload zone (or use the generated `data/synthetic/dirty_dataset.csv`).
2. Watch the **Results** tab render a confidence-colored table; click any cell for its full cleaning lineage.
3. Correct a low-confidence cell — its confidence jumps to 100% and the correction is remembered for next time.
4. Check the **AI Insights** tab for correlations/trends/anomalies with charts.
5. Add a rule under the **Rules** tab (e.g. "age must be ≥ 0") — it's immediately checked against this upload *and* every upload already sitting in the database.
6. Download the cleaned CSV from the toolbar.

---

## Running the test suite

```bash
source .venv/bin/activate
pytest
```
281 tests covering every agent, the API, the DB layer, the rule engine, and a dedicated robustness suite that runs the full pipeline against several completely unrelated dataset shapes to guarantee nothing crashes on an unfamiliar file.

---

## Project layout

```
backend/   FastAPI app — agents, orchestrator, insights, rule engine, DB layer, LLM client
frontend/  React (Vite) UI — routed per tab, code-split
data/      SQLite DB file + generated synthetic datasets + uploaded raw files
db/        Alembic migrations (env.py, versions/) — alembic.ini lives at repo root
tests/     pytest suite (281 tests)
```

See **[TECHNICAL_REPORT.md](TECHNICAL_REPORT.md)** for the complete architecture, every agent's design, the full test breakdown, and a list of real bugs found and fixed while building this.
