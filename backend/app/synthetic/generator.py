"""Phase 1 — Synthetic "dirty" customer/order dataset generator.

Deterministic (seedable): the same seed always reproduces the exact same
dataset, including which rows carry which intentional defect, so testing
against it is repeatable. See data_dictionary.md (written alongside the CSV)
for a human-readable account of what was broken and why.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import phonenumbers
from faker import Faker

from app.common.turkey_geo import COUNTRY, PROVINCE_DISTRICTS
from app.synthetic.corruption import (
    ambiguous_date_string,
    dirty_address,
    dirty_age,
    dirty_currency,
    dirty_date,
    dirty_email,
    dirty_phone,
    maybe_sql_injection_name,
)
from app.synthetic.reference_data import (
    CATEGORIES,
    SEGMENT_CONFIG,
    SEGMENT_WEIGHTS,
    SQL_INJECTION_PAYLOADS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "synthetic"

END_DATE = date(2026, 6, 30)  # fixed, not tied to real "today" — keeps output stable
ORDER_HISTORY_MONTHS = 18

_TR_ASCII_MAP = str.maketrans(
    "çÇğĞıİöÖşŞüÜ", "cCgGiIoOsSuU"
)


def _ascii_slug(name: str) -> str:
    ascii_name = name.translate(_TR_ASCII_MAP).lower()
    ascii_name = re.sub(r"[^a-z0-9\s]", "", ascii_name)  # drop punctuation (titles like "Arş. Gör.")
    return re.sub(r"\s+", ".", ascii_name.strip())


@dataclass
class Customer:
    customer_id: int
    name_true: str
    email_true: str
    phone_true_digits: str
    signup_date_true: date
    age_true: int
    segment: str
    city: str
    district: str
    sql_injection_payload: str | None = None


@dataclass
class Order:
    customer_id: int
    order_date_true: date
    category: str
    amount_true: float
    is_outlier: bool = False
    is_ambiguous_date: bool = False


def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights.keys())
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def _generate_valid_mobile_digits(rng: random.Random) -> str:
    """A random 10-digit Turkish mobile number (no leading 0/+90) that is
    actually valid per libphonenumber — not just any '5' + 9 random digits,
    which mostly aren't (Turkish mobile prefixes only use specific 2nd/3rd
    digit combinations). Rejection sampling, deterministic under `rng`."""
    while True:
        candidate = "5" + "".join(rng.choice("0123456789") for _ in range(9))
        parsed = phonenumbers.parse("+90" + candidate, "TR")
        if phonenumbers.is_valid_number(parsed):
            return candidate


def _generate_customers(n: int, rng: random.Random, fake: Faker) -> list[Customer]:
    customers = []
    for i in range(1, n + 1):
        name = fake.name()
        province = rng.choice(list(PROVINCE_DISTRICTS.keys()))
        district = rng.choice(PROVINCE_DISTRICTS[province])
        customers.append(
            Customer(
                customer_id=i,
                name_true=name,
                email_true=f"{_ascii_slug(name)}{i}@example.com",
                phone_true_digits=_generate_valid_mobile_digits(rng),
                signup_date_true=END_DATE - timedelta(days=rng.randint(200, 1800)),
                age_true=rng.randint(18, 75),
                segment=_weighted_choice(rng, SEGMENT_WEIGHTS),
                city=province,
                district=district,
            )
        )
    return customers


def _amount_for(age: int, segment: str, rng: random.Random) -> float:
    base = 30.0
    age_effect = 5.5 * age
    segment_adjustment = SEGMENT_CONFIG[segment]["amount_adjustment"]
    noise = rng.gauss(0, 18)
    return max(8.0, base + age_effect + segment_adjustment + noise)


def _generate_orders(customers: list[Customer], rng: random.Random) -> list[Order]:
    orders: list[Order] = []
    for c in customers:
        lo, hi = SEGMENT_CONFIG[c.segment]["orders_per_customer"]
        n_orders = rng.randint(lo, hi)
        window_start = max(c.signup_date_true, END_DATE - timedelta(days=30 * ORDER_HISTORY_MONTHS))
        window_days = max(1, (END_DATE - window_start).days)
        for _ in range(n_orders):
            order_date = window_start + timedelta(days=rng.randint(0, window_days))
            category = rng.choice(CATEGORIES)
            amount = _amount_for(c.age_true, c.segment, rng)
            orders.append(Order(customer_id=c.customer_id, order_date_true=order_date,
                                 category=category, amount_true=amount))
    return orders


def _inject_seasonal_anomaly(orders: list[Order], customers: list[Customer],
                              rng: random.Random) -> dict:
    anomaly_category = "Electronics"
    anomaly_month_date = END_DATE - timedelta(days=30 * 7)  # a month clear of both edges
    anomaly_year, anomaly_month = anomaly_month_date.year, anomaly_month_date.month

    def month_key(d: date) -> tuple[int, int]:
        return d.year, d.month

    monthly_counts: dict[tuple[int, int], int] = {}
    for o in orders:
        if o.category == anomaly_category:
            monthly_counts[month_key(o.order_date_true)] = monthly_counts.get(month_key(o.order_date_true), 0) + 1
    other_months = {k: v for k, v in monthly_counts.items() if k != (anomaly_year, anomaly_month)}
    normal_avg = sum(other_months.values()) / len(other_months) if other_months else 10

    current_spike_count = monthly_counts.get((anomaly_year, anomaly_month), 0)
    target_count = round(normal_avg * 2.75)
    to_add = max(0, target_count - current_spike_count)

    customer_ids = [c.customer_id for c in customers]
    customers_by_id = {c.customer_id: c for c in customers}
    days_in_month = 28
    for _ in range(to_add):
        cid = rng.choice(customer_ids)
        c = customers_by_id[cid]
        order_date = date(anomaly_year, anomaly_month, rng.randint(1, days_in_month))
        amount = _amount_for(c.age_true, c.segment, rng)
        orders.append(Order(customer_id=cid, order_date_true=order_date,
                             category=anomaly_category, amount_true=amount))

    return {
        "category": anomaly_category,
        "month": f"{anomaly_year:04d}-{anomaly_month:02d}",
        "normal_avg_monthly_count": round(normal_avg, 1),
        "spike_month_count": current_spike_count + to_add,
        "spike_multiplier": round((current_spike_count + to_add) / normal_avg, 2) if normal_avg else None,
    }


def _inject_outliers(orders: list[Order], rng: random.Random, n: int = 5) -> list[int]:
    # Large enough to be an unmistakable Z-score/IQR outlier, small enough
    # that a handful of points don't single-handedly erase the dataset-wide
    # age/amount correlation the Correlation Agent is meant to find.
    indices = rng.sample(range(len(orders)), k=min(n, len(orders)))
    for idx in indices:
        orders[idx].amount_true *= rng.uniform(8, 18)
        orders[idx].is_outlier = True
    return indices


def _inject_ambiguous_dates(orders: list[Order], rng: random.Random, fraction: float = 0.015) -> list[int]:
    candidates = [i for i, o in enumerate(orders) if o.order_date_true.day <= 12]
    n = max(3, round(len(orders) * fraction))
    chosen = rng.sample(candidates, k=min(n, len(candidates)))
    for idx in chosen:
        orders[idx].is_ambiguous_date = True
    return chosen


def _inject_sql_injection(customers: list[Customer], rng: random.Random) -> list[int]:
    chosen = rng.sample(range(len(customers)), k=min(len(SQL_INJECTION_PAYLOADS), len(customers)))
    for i, cust_idx in enumerate(chosen):
        customers[cust_idx].sql_injection_payload = SQL_INJECTION_PAYLOADS[i]
    return [customers[i].customer_id for i in chosen]


def generate_dataset(seed: int = 42, n_customers: int = 500) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    rng = random.Random(seed)
    Faker.seed(seed)
    fake = Faker("tr_TR")

    customers = _generate_customers(n_customers, rng, fake)
    orders = _generate_orders(customers, rng)

    seasonal_info = _inject_seasonal_anomaly(orders, customers, rng)
    _inject_outliers(orders, rng)
    _inject_ambiguous_dates(orders, rng)
    _inject_sql_injection(customers, rng)

    customers_by_id = {c.customer_id: c for c in customers}
    true_ages = np.array([customers_by_id[o.customer_id].age_true for o in orders])
    true_amounts = np.array([o.amount_true for o in orders])
    pearson_r = float(np.corrcoef(true_ages, true_amounts)[0, 1])

    rng.shuffle(orders)

    rows = []
    ground_truth_rows = []
    sql_injection_row_indices = []
    ambiguous_row_indices = []
    outlier_row_indices = []

    for row_idx, o in enumerate(orders):
        c = customers_by_id[o.customer_id]
        name_out = maybe_sql_injection_name(c.name_true, c.sql_injection_payload, rng)
        if c.sql_injection_payload is not None:
            sql_injection_row_indices.append(row_idx)

        order_date_str = (
            ambiguous_date_string(o.order_date_true, rng)
            if o.is_ambiguous_date
            else dirty_date(o.order_date_true, rng)
        )
        if o.is_ambiguous_date:
            ambiguous_row_indices.append(row_idx)
        if o.is_outlier:
            outlier_row_indices.append(row_idx)

        amount_str, currency_hint = dirty_currency(o.amount_true, rng)

        rows.append({
            "customer_id": c.customer_id,
            "name": name_out,
            "email": dirty_email(c.email_true, rng),
            "phone": dirty_phone(c.phone_true_digits, rng),
            "signup_date": dirty_date(c.signup_date_true, rng),
            "order_date": order_date_str,
            "address": dirty_address(c.city, c.district, rng),
            "order_amount": amount_str,
            "currency_hint": currency_hint,
            "customer_age": dirty_age(c.age_true, rng),
            "category": o.category,
            "customer_segment": c.segment,
        })
        ground_truth_rows.append({
            "row_index": row_idx,
            "customer_id": c.customer_id,
            "name_true": c.name_true,
            "email_true": c.email_true,
            "phone_true_e164": f"+90{c.phone_true_digits}",
            "signup_date_true": c.signup_date_true.isoformat(),
            "order_date_true": o.order_date_true.isoformat(),
            "city_true": c.city,
            "district_true": c.district,
            "country_true": COUNTRY,
            "order_amount_true": round(o.amount_true, 2),
            "customer_age_true": c.age_true,
        })

    df = pd.DataFrame(rows, columns=[
        "customer_id", "name", "email", "phone", "signup_date", "order_date",
        "address", "order_amount", "currency_hint", "customer_age", "category",
        "customer_segment",
    ])
    ground_truth_df = pd.DataFrame(ground_truth_rows)

    avg_orders_by_segment = {
        seg: round(sum(1 for o in orders if customers_by_id[o.customer_id].segment == seg) /
                    max(1, sum(1 for c in customers if c.segment == seg)), 2)
        for seg in SEGMENT_CONFIG
    }
    avg_amount_by_segment = {
        seg: round(float(np.mean([o.amount_true for o in orders if customers_by_id[o.customer_id].segment == seg])), 2)
        for seg in SEGMENT_CONFIG
        if any(customers_by_id[o.customer_id].segment == seg for o in orders)
    }

    manifest = {
        "seed": seed,
        "n_customers": n_customers,
        "n_orders": len(orders),
        "sql_injection_rows": [
            {"row_index": i, "customer_id": int(df.iloc[i]["customer_id"]), "column": "name",
             "payload": df.iloc[i]["name"]}
            for i in sql_injection_row_indices
        ],
        "ambiguous_date_rows": [
            {"row_index": i, "column": "order_date", "displayed_value": df.iloc[i]["order_date"],
             "true_date": ground_truth_df.iloc[i]["order_date_true"]}
            for i in ambiguous_row_indices
        ],
        "outlier_rows": [
            {"row_index": i, "displayed_value": df.iloc[i]["order_amount"],
             "true_amount": ground_truth_df.iloc[i]["order_amount_true"]}
            for i in outlier_row_indices
        ],
        "seasonal_anomaly": seasonal_info,
        "age_amount_correlation": {
            "pearson_r": round(pearson_r, 3),
            "description": "customer_age vs order_amount, computed on true (pre-corruption) values",
        },
        "segment_pattern": {
            "description": (
                "Budget customers order most frequently but at the lowest average "
                "amount; Premium customers order least frequently but at the "
                "highest average amount — a counter-intuitive relationship for "
                "the insight layer to surface."
            ),
            "avg_orders_per_customer_by_segment": avg_orders_by_segment,
            "avg_amount_by_segment": avg_amount_by_segment,
        },
    }
    return df, ground_truth_df, manifest


def _write_data_dictionary(manifest: dict, path: Path) -> None:
    lines = [
        "# Synthetic Dataset — Data Dictionary",
        "",
        f"Generated with seed `{manifest['seed']}` — regenerate with:",
        f"`python -m app.synthetic.generator --seed {manifest['seed']} --customers {manifest['n_customers']}`",
        "",
        f"Rows: {manifest['n_orders']} orders across {manifest['n_customers']} customers.",
        "",
        "## Columns",
        "`customer_id, name, email, phone, signup_date, order_date, address, "
        "order_amount, currency_hint, customer_age, category, customer_segment`",
        "",
        "## Intentionally broken — and why",
        "",
        "### Mixed date formats (`signup_date`, `order_date`)",
        "ISO (`YYYY-MM-DD`), `DD/MM/YYYY`, `MM-DD-YYYY`, and `\"Month D, YYYY\"` "
        "are all present, weighted so ISO is the majority format. Tests the Date "
        "Agent's format detection and majority-pattern inference.",
        "",
        "### Genuinely ambiguous dates",
        f"{len(manifest['ambiguous_date_rows'])} rows have an `order_date` where day "
        "and month are both ≤ 12 (e.g. `03/04/2024`) — unresolvable from that row "
        "alone. These should come out of the Date Agent with **low confidence**, "
        "not a silent guess. See `manifest.json` → `ambiguous_date_rows` for exact "
        "row indices and displayed values.",
        "",
        "### Mixed currency formats (`order_amount`, `currency_hint`)",
        "Four display styles are used: `$120.50` (USD), `1.500 TL` (dot thousands "
        "separator), `85,50` (bare comma-decimal, `currency_hint` left blank — "
        "genuinely ambiguous currency), and `₺2000` (TRY symbol). Tests the "
        "Currency Agent's symbol/format detection and decimal-separator handling.",
        "",
        "### Malformed phone numbers (`phone`)",
        "Missing `+90` country code, extra/irregular spacing, a single mistyped "
        "digit, or a missing digit — one style picked per row.",
        "",
        "### Malformed emails (`email`)",
        "Typo domains (`gmial.com`, `hotnail.com`, ...) or stray leading/trailing "
        "whitespace on an otherwise valid address.",
        "",
        "### Inconsistent address order (`address`)",
        "Either `\"District - City - Country\"` or `\"Country, City, District\"`, "
        "with the district or country occasionally dropped entirely. Built from a "
        "real lookup of Turkish provinces/districts "
        "(`backend/app/common/turkey_geo.py`), the same table Phase 4's Address "
        "Agent uses for its rule-based resolution.",
        "",
        "### Messy ages (`customer_age`)",
        "Mostly plain integers; some padded with whitespace (`\" 35 \"`), some "
        "spelled out (`\"thirty-five\"`), a few blank. Mirrors the Phase 5 lineage "
        "example (`\" thirty-five \"` → `35`, 98% confidence).",
        "",
        f"### SQL-injection payloads in `name` ({len(manifest['sql_injection_rows'])} rows)",
        "e.g. `Robert'); DROP TABLE customers;--`. Purely to prove the storage "
        "layer (Phase 2, parameterized queries only) treats this as inert text, "
        "never as SQL. Row indices and payloads: see `manifest.json` → "
        "`sql_injection_rows`.",
        "",
        f"### Extreme outliers ({len(manifest['outlier_rows'])} rows)",
        "`order_amount` inflated 8-18x for a handful of rows — large enough to "
        "be an unmistakable Z-score/IQR outlier without single-handedly erasing "
        "the dataset-wide correlation below. See `manifest.json` → "
        "`outlier_rows`.",
        "",
        "## Embedded statistical relationships (for the Insight layer, Phase 7)",
        "",
        f"- **Positive correlation:** `customer_age` vs `order_amount`, "
        f"r = {manifest['age_amount_correlation']['pearson_r']} (computed on true, "
        "pre-corruption values — the Correlation Agent should recover something "
        "close to this once the columns are cleaned).",
        f"- **Seasonal anomaly:** `{manifest['seasonal_anomaly']['category']}` "
        f"orders spike to {manifest['seasonal_anomaly']['spike_multiplier']}x the "
        f"normal monthly average ({manifest['seasonal_anomaly']['normal_avg_monthly_count']} "
        f"→ {manifest['seasonal_anomaly']['spike_month_count']}) in "
        f"{manifest['seasonal_anomaly']['month']}.",
        "- **Counter-intuitive segment pattern:** "
        f"{manifest['segment_pattern']['description']} "
        f"(avg orders/customer: {manifest['segment_pattern']['avg_orders_per_customer_by_segment']}; "
        f"avg amount: {manifest['segment_pattern']['avg_amount_by_segment']}).",
        "",
        "## Files",
        "- `dirty_dataset.csv` — the actual messy dataset to upload through the app.",
        "- `ground_truth.csv` — true pre-corruption values, row-aligned to "
        "`dirty_dataset.csv` by `row_index`. For our own automated testing of the "
        "cleaning agents' accuracy — not something the app itself reads.",
        "- `manifest.json` — machine-readable index of every intentional defect "
        "and embedded relationship above, by exact row index.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Phase 1 synthetic dirty dataset.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--customers", type=int, default=500)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    df, ground_truth_df, manifest = generate_dataset(seed=args.seed, n_customers=args.customers)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out_dir / "dirty_dataset.csv", index=False)
    ground_truth_df.to_csv(args.out_dir / "ground_truth.csv", index=False)
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_data_dictionary(manifest, args.out_dir / "data_dictionary.md")

    print(f"Wrote {len(df)} rows to {args.out_dir / 'dirty_dataset.csv'}")
    print(f"age/amount correlation (true values): r = {manifest['age_amount_correlation']['pearson_r']}")
    print(f"seasonal anomaly: {manifest['seasonal_anomaly']}")


if __name__ == "__main__":
    main()
