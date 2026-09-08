"""Functions that take a "true" clean value and return an intentionally
messy string, per the formats called out in the Phase 1 roadmap section.
Every function takes a seeded `random.Random` so the whole dataset is
reproducible from one seed.
"""
from __future__ import annotations

import random
from datetime import date

from app.common.turkey_geo import COUNTRY
from app.synthetic.reference_data import NUMBER_WORDS, TYPO_EMAIL_DOMAINS

# Weighted so a real majority format exists (agents can use "majority pattern
# in the column" as a signal), with a long tail of alternate formats.
DATE_FORMAT_WEIGHTS = [
    ("%Y-%m-%d", 0.40),
    ("%d/%m/%Y", 0.25),
    ("%m-%d-%Y", 0.20),
    ("%B %d, %Y", 0.15),
]


def dirty_date(d: date, rng: random.Random) -> str:
    fmt = rng.choices(
        [f for f, _ in DATE_FORMAT_WEIGHTS],
        weights=[w for _, w in DATE_FORMAT_WEIGHTS],
        k=1,
    )[0]
    return d.strftime(fmt)


def ambiguous_date_string(d: date, rng: random.Random) -> str:
    """Only valid when day and month are both <=12 — the row genuinely can't
    be disambiguated without column-level context. Caller guarantees that."""
    fmt = rng.choice(["%d/%m/%Y", "%m/%d/%Y"])
    return d.strftime(fmt)


def _format_dot_thousands(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def dirty_currency(amount: float, rng: random.Random) -> tuple[str, str]:
    """Returns (order_amount string, currency_hint string)."""
    style = rng.choice(["usd_dollar", "try_tl_suffix", "bare_comma", "try_symbol"])
    if style == "usd_dollar":
        return f"${amount:.2f}", "USD"
    if style == "try_tl_suffix":
        return f"{_format_dot_thousands(round(amount))} TL", "TRY"
    if style == "bare_comma":
        # No symbol at all — ambiguous currency, forces the agent to infer.
        return f"{amount:.2f}".replace(".", ","), ""
    return f"₺{round(amount)}", "TRY"


PHONE_STYLES = ["full_e164_spaced", "no_country_code", "extra_spaces", "typo_digit", "missing_digit"]


def dirty_phone(true_digits: str, rng: random.Random) -> str:
    """`true_digits` is 10 digits, e.g. '5XXXXXXXXX' (Turkish mobile, no
    leading 0/+90)."""
    style = rng.choice(PHONE_STYLES)
    d = true_digits
    if style == "full_e164_spaced":
        return f"+90 {d[0:3]} {d[3:6]} {d[6:8]} {d[8:10]}"
    if style == "no_country_code":
        return f"0{d[0:3]} {d[3:6]} {d[6:8]} {d[8:10]}"
    if style == "extra_spaces":
        return f"+90  {d[0:3]}   {d[3:6]} {d[6:8]}  {d[8:10]} "
    if style == "typo_digit":
        pos = rng.randrange(len(d))
        digits = list(d)
        digits[pos] = rng.choice([c for c in "0123456789" if c != digits[pos]])
        return f"+90{''.join(digits)}"
    # missing_digit
    pos = rng.randrange(len(d))
    return f"+90{d[:pos]}{d[pos + 1:]}"


def dirty_email(true_email: str, rng: random.Random) -> str:
    style = rng.choice(["typo_domain", "extra_spaces", "as_is"])
    local, _, domain = true_email.partition("@")
    if style == "typo_domain":
        return f"{local}@{rng.choice(TYPO_EMAIL_DOMAINS)}"
    if style == "extra_spaces":
        return f" {local}@{domain} "
    return true_email


def maybe_sql_injection_name(true_name: str, payload: str | None, rng: random.Random) -> str:
    return payload if payload is not None else true_name


def dirty_address(city: str, district: str, rng: random.Random) -> str:
    drop_district = rng.random() < 0.15
    drop_country = rng.random() < 0.10
    parts_country_first = [COUNTRY, city] + ([] if drop_district else [district])
    parts_district_first = ([] if drop_district else [district]) + [city] + ([] if drop_country else [COUNTRY])

    if rng.random() < 0.5:
        parts = parts_country_first if not drop_country else parts_country_first[1:]
        return ", ".join(parts)
    return " - ".join(parts_district_first)


def dirty_age(true_age: int, rng: random.Random) -> str:
    style = rng.choices(
        ["plain", "padded", "spelled_out", "empty"],
        weights=[0.75, 0.12, 0.10, 0.03],
        k=1,
    )[0]
    if style == "padded":
        return f" {true_age} "
    if style == "spelled_out" and true_age in NUMBER_WORDS:
        return NUMBER_WORDS[true_age]
    if style == "empty":
        return ""
    return str(true_age)
