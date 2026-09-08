# Synthetic Dataset — Data Dictionary

Generated with seed `42` — regenerate with:
`python -m app.synthetic.generator --seed 42 --customers 500`

Rows: 3432 orders across 500 customers.

## Columns
`customer_id, name, email, phone, signup_date, order_date, address, order_amount, currency_hint, customer_age, category, customer_segment`

## Intentionally broken — and why

### Mixed date formats (`signup_date`, `order_date`)
ISO (`YYYY-MM-DD`), `DD/MM/YYYY`, `MM-DD-YYYY`, and `"Month D, YYYY"` are all present, weighted so ISO is the majority format. Tests the Date Agent's format detection and majority-pattern inference.

### Genuinely ambiguous dates
51 rows have an `order_date` where day and month are both ≤ 12 (e.g. `03/04/2024`) — unresolvable from that row alone. These should come out of the Date Agent with **low confidence**, not a silent guess. See `manifest.json` → `ambiguous_date_rows` for exact row indices and displayed values.

### Mixed currency formats (`order_amount`, `currency_hint`)
Four display styles are used: `$120.50` (USD), `1.500 TL` (dot thousands separator), `85,50` (bare comma-decimal, `currency_hint` left blank — genuinely ambiguous currency), and `₺2000` (TRY symbol). Tests the Currency Agent's symbol/format detection and decimal-separator handling.

### Malformed phone numbers (`phone`)
Missing `+90` country code, extra/irregular spacing, a single mistyped digit, or a missing digit — one style picked per row.

### Malformed emails (`email`)
Typo domains (`gmial.com`, `hotnail.com`, ...) or stray leading/trailing whitespace on an otherwise valid address.

### Inconsistent address order (`address`)
Either `"District - City - Country"` or `"Country, City, District"`, with the district or country occasionally dropped entirely. Built from a real lookup of Turkish provinces/districts (`backend/app/common/turkey_geo.py`), the same table Phase 4's Address Agent uses for its rule-based resolution.

### Messy ages (`customer_age`)
Mostly plain integers; some padded with whitespace (`" 35 "`), some spelled out (`"thirty-five"`), a few blank. Mirrors the Phase 5 lineage example (`" thirty-five "` → `35`, 98% confidence).

### SQL-injection payloads in `name` (15 rows)
e.g. `Robert'); DROP TABLE customers;--`. Purely to prove the storage layer (Phase 2, parameterized queries only) treats this as inert text, never as SQL. Row indices and payloads: see `manifest.json` → `sql_injection_rows`.

### Extreme outliers (5 rows)
`order_amount` inflated 8-18x for a handful of rows — large enough to be an unmistakable Z-score/IQR outlier without single-handedly erasing the dataset-wide correlation below. See `manifest.json` → `outlier_rows`.

## Embedded statistical relationships (for the Insight layer, Phase 7)

- **Positive correlation:** `customer_age` vs `order_amount`, r = 0.54 (computed on true, pre-corruption values — the Correlation Agent should recover something close to this once the columns are cleaned).
- **Seasonal anomaly:** `Electronics` orders spike to 2.74x the normal monthly average (22.6 → 62) in 2025-12.
- **Counter-intuitive segment pattern:** Budget customers order most frequently but at the lowest average amount; Premium customers order least frequently but at the highest average amount — a counter-intuitive relationship for the insight layer to surface. (avg orders/customer: {'Budget': 12.2, 'Regular': 5.38, 'Premium': 2.76, 'New': 1.56}; avg amount: {'Budget': 242.2, 'Regular': 301.72, 'Premium': 440.39, 'New': 291.59}).

## Files
- `dirty_dataset.csv` — the actual messy dataset to upload through the app.
- `ground_truth.csv` — true pre-corruption values, row-aligned to `dirty_dataset.csv` by `row_index`. For our own automated testing of the cleaning agents' accuracy — not something the app itself reads.
- `manifest.json` — machine-readable index of every intentional defect and embedded relationship above, by exact row index.
