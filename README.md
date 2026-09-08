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

## Phase 4 — Cleaning Agents (done)

Five agents (`backend/app/agents/`), each returning a cleaned value + a 0-100
confidence score. Four are fully rule-based; the Address Agent is the one
deliberate LLM use in the whole pipeline:

- **DateAgent** — regex + `dateutil`, normalizes to ISO 8601. An ambiguous
  `DD/MM` vs `MM/DD` row is resolved from the column's own majority pattern
  (established from other rows where one component is unambiguously >12);
  with no such evidence anywhere in the column, it still produces a best
  guess but scores it Low and flags it — visibly a guess, never silent.
- **CurrencyAgent** — regex-detects `$`/`₺`/`TL` and the bare comma-decimal
  style, normalizes the number, reports the currency code separately. A
  bare number with no symbol falls back to `currency_hint` or a stated
  default (TRY) at Medium confidence.
- **ContactAgent** — `phonenumbers` (libphonenumber) for phone,
  `email-validator` for email. A value that can't be confidently fixed is
  flagged, never silently dropped.
- **NumericAgent** — handles `customer_age`, including the exact Phase 5
  example (`" thirty-five "` → `35`, 98%, `text_to_number_pattern`), and
  flags implausible values (e.g. age 999) rather than passing them through.
- **AddressAgent** — rule-based lookup against `turkey_geo.py` first (zero
  LLM cost, resolves ~90% of our synthetic addresses). Only the residual
  cases fall back to an LLM call (`backend/app/llm/client.py`, isolated
  behind `AnthropicLLMClient` — nothing else in the codebase imports
  `anthropic` directly), constrained to a Pydantic schema and
  re-validated against the same lookup table before being trusted — a
  hallucinated province/district is rejected even if it's schema-valid.
  The untrusted address text is always sent as an isolated JSON data field,
  never concatenated into the prompt, so injected instructions in a cell
  can't be mistaken for commands.

Every agent's per-row cleaner is wrapped so a single bad row can never
crash a batch — any unhandled exception becomes a flagged, zero-confidence
result instead, and every decision (rule-based or LLM) is written to the
Phase 0 audit trail.

While wiring these up against the real synthetic dataset, found and fixed a
Phase 1 generator bug: base phone numbers were "5" + 9 fully random digits,
but Turkish mobile numbers only use specific prefix combinations — only
~51% of generated numbers were valid *before* any intentional dirtying was
even applied. Fixed with rejection sampling against `phonenumbers` so the
dataset's phone corruption rate now reflects the intentional styles
(missing/mistyped digit) rather than an artifact of generation.

## Phase 5 — Trust Score & Data Lineage (done)

Two new pieces tie everything together into something you can actually
open in a browser:

- **`backend/app/pipeline.py`** — the glue between Phases 3/4 (routing +
  agents, both in-memory) and Phase 2 (persistence): validates an upload,
  runs the orchestrator, cleans every routed column, and writes the whole
  result as one transaction. Extended the schema with a nullable
  `audit_log.record_id` FK so every cleaned cell links directly to its own
  audit trail entries — what the lineage drill-down queries.
- **`backend/app/api/`** — FastAPI routes: `POST /api/uploads` (upload →
  full cleaning run), `GET /api/uploads/{id}/cleaned-records` (paginated,
  filterable by column and confidence threshold), `GET
  /api/uploads/{id}/records/{id}/lineage` (per-cell pipeline history).
- **`frontend/`** — a React (Vite) single page: upload a CSV, see the
  cleaned data as a table with cells colored by confidence (green ≥90,
  yellow 60-89, red <60), filter to "only cells below confidence X" with a
  clearly-labeled empty state when nothing matches, and click any cell to
  open a lineage panel showing its original/cleaned value, confidence, and
  full pipeline history — or "No lineage recorded" if there isn't one.

```bash
# backend
source .venv/bin/activate && cd backend && uvicorn app.main:app --reload
# frontend (separate terminal)
cd frontend && npm install && npm run dev
```

Verified with 23 new backend tests (pipeline + API, 110/110 total passing)
and a real browser run (Playwright-driven Chromium) against the actual
3,432-row synthetic dataset: uploaded it through the UI, confirmed
color-coded cells render, the confidence filter narrows results correctly
and shows the empty state at an impossible threshold, and the lineage
panel opens with real pipeline history — including the NumericAgent's
`"fifty-eight"` → `58` (98%) and `"forty"` → `40` (98%) rows rendering
exactly as designed. No browser console errors.

Caught and fixed one real bug from that browser run: a blank CSV cell
arrives as pandas `NaN` (a float), not Python `None` — `str(nan)` is the
text `"nan"`, which was being stored and displayed as if that were the
literal original value. Fixed in the pipeline's DB-boundary conversion,
with a regression test.

## Phase 6 — Self-Improving Feedback Loop (done)

When a user corrects a low-confidence cell (`POST
/api/uploads/{id}/records/{id}/correction`, and now a "Correct this value"
box right in the Phase 5 lineage panel), three things happen atomically:
the `cleaned_records` row updates to 100% confidence, the correction is
upserted into `feedback_corrections`, and a linked `UserFeedback` audit
entry records it — all visible immediately in that cell's lineage history.

Before cleaning a column, the pipeline loads every prior correction for
that value type (`date`, `phone`, `email`, ...) into an in-memory map once,
and every agent checks it first — a raw value matching a prior correction
(exact, or "near-identical": same after trimming whitespace/case) is reused
directly at high confidence, skipping the normal cleaning logic entirely,
at zero additional cost. For the Address Agent specifically, when a value
has no lookup or feedback match and falls through to the LLM, up to 3 prior
corrections are included as few-shot examples — still sent as an isolated
data field, never folded into the instructions. If the feedback table is
empty or the lookup query itself fails, cleaning proceeds normally (this
is tested explicitly, not just assumed).

Verified with 20 new tests (132/132 total) including a full round-trip: an
upload gets an ambiguous date, a user corrects it, a second independent
upload with the exact same raw value resolves it from feedback with no
re-guessing. Also verified live in the browser — corrected a cell, watched
its confidence jump to 100% and a new `UserFeedback` entry appear in its
lineage history in real time.

Also found and fixed a real robustness bug while testing this live: three
repository functions (`create_raw_upload`, `upsert_feedback_correction`,
`submit_correction`, `create_audit_log`) called `db.refresh()` right after
`db.commit()` — needless, since the session is `expire_on_commit=False`
and the object is already valid in-memory post-commit. Under concurrent
load that extra round-trip could itself fail, incorrectly turning an
already-successful write into a reported 500. Removed; the write's success
no longer depends on a follow-up read succeeding.

## Phase 7 — "AI Insight" Layer (done)

Four agents in `backend/app/insights/`, enforcing the roadmap's mandatory
pipeline: raw data → deterministic statistics (pandas/numpy, no LLM) →
Narrative Agent (LLM, phrasing only, never computes a number) → explanation.

- **CorrelationAgent** — `pandas.corr()` on numeric columns, filtered to
  `|r| > 0.4`, banded weak/moderate/strong by a fixed rule.
- **TrendAgent** — two views: overall month-over-month sum (e.g. revenue),
  and per-category monthly counts compared to that category's own other-month
  average (spike detection — this is what finds a seasonal anomaly regardless
  of which month it happens to land next to).
- **AnomalyAgent** — IQR-based (robust to the outliers it's detecting, unlike
  plain z-score/std), with a z-score computed per flagged point purely for
  the "how far above normal" narrative phrasing.
- **NarrativeAgent** — the one LLM use here. A hallucination guard rejects
  any output mentioning a number that doesn't trace back to the given
  statistics (with tolerance for legitimate rephrasing, e.g. `r=0.71` → "71%"),
  falling back to a template sentence — which happens automatically whenever
  the LLM is unavailable, since there's no API key configured in this
  environment. The causation disclaimer is appended by code, unconditionally,
  to every correlation narrative — never left to the LLM to remember.

Computing insights needs the *original* dataset, including columns no agent
classified (e.g. `category`) — those never reach `cleaned_records`, only
existing transiently in memory during the cleaning run. So insights are
computed once, synchronously, right after cleaning (see `pipeline.py`), and
cached as JSON on `raw_uploads.insights_json` — a failure there can't fail
the upload that already succeeded, and a cache miss is an "insights not
available yet" state, never an error.

Verified end-to-end against the real 3,432-row dataset: found the embedded
age/amount correlation (r=0.54), the Electronics seasonal spike (+209% in
December, matching the ~2.5-3x the generator targets), and 5 of the
generator's extreme-outlier orders — all three signals the dataset was
built to contain.

While verifying live, found and fixed two real signal-vs-noise bugs: (1) a
pandas 3.0 dtype change (`is_object_dtype` no longer matches its new default
string dtype) silently broke the category-column auto-detection entirely;
(2) a handful of misinterpreted ambiguous dates create a long tail of
near-empty trailing months, which showed up as dozens of fake "-95%" swings
drowning out the one real signal — fixed by requiring the *flagged* period's
own row count to clear a floor, not just the baseline being compared against.

## Phase 8 — Visualization Layer (done)

Chart-ready data is computed alongside the cards above (same DataFrame, no
extra queries) and rendered with `recharts`: a correlation heatmap (full
pairwise matrix, not just the significant pairs), a scatter plot with a
linear-regression trend line for the top relationship, a line chart for the
trend column's full monthly series, a bar chart comparing categories, and a
scatter plot with anomalies highlighted in red against normal points in gray.
Every chart shows a "not enough data" state instead of rendering broken when
the underlying data's insufficient — same pattern as the Phase 5 heatmap's
empty state.

## Phase 9 — Web Interface (done)

Tied everything into one app: a drag-and-drop upload zone (client-side
size/type validation against `GET /api/config`, so the frontend can never
drift out of sync with what the backend actually enforces) → a processing
summary (per-agent row/flagged counts — the backend cleans synchronously
within one request, so this is a completion summary rather than a live
stream) → a Results/AI Insights tab switcher over the Phase 5 trust heatmap
and the Phase 7/8 insight cards and charts. React escapes all rendered text
by default (no `dangerouslySetInnerHTML` anywhere), so XSS from a malicious
cell value is a non-issue structurally, not just by convention. The optional
ad-hoc chat feature was deliberately skipped — explicitly optional in the
roadmap, and out of scope for the time remaining.

Verified live end-to-end in a real browser: uploaded a `.txt` file and
confirmed it's rejected client-side before ever reaching the API; uploaded
the real dataset and watched the processing summary, results heatmap, and
insights tab (cards, explain modal with the causation disclaimer, all five
charts) all render correctly with zero console errors.
