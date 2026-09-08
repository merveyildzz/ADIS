"""Contact Agent — no LLM. Validates/normalizes phone numbers with
`phonenumbers` (libphonenumber) and emails with `email-validator`. Values
that can't be confidently fixed are flagged, never silently dropped.
"""
from __future__ import annotations

import pandas as pd
import phonenumbers
from email_validator import EmailNotValidError, EmailSyntaxError, validate_email
from phonenumbers import NumberParseException

from app.agents.base import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, AgentResult, check_feedback, safe_clean_row

AGENT_TYPE = "ContactAgent"

# Our synthetic data (and the real-world scope this agent targets) is
# Turkish phone numbers — used as the default region when a value has no
# explicit country code (e.g. a leading 0 instead of +90).
DEFAULT_REGION = "TR"


@safe_clean_row(AGENT_TYPE)
def clean_phone_value(raw_value: str) -> AgentResult:
    value = raw_value.strip()
    try:
        parsed = phonenumbers.parse(value, DEFAULT_REGION)
    except NumberParseException:
        return AgentResult(value, None, 0.0, "unparseable_phone", AGENT_TYPE, flagged=True)

    if phonenumbers.is_valid_number(parsed):
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        confidence = CONFIDENCE_HIGH if value.replace(" ", "") == e164 else CONFIDENCE_MEDIUM
        method = "valid_e164" if confidence == CONFIDENCE_HIGH else "normalized_to_e164"
        return AgentResult(value, e164, confidence, method, AGENT_TYPE)

    return AgentResult(value, None, 0.0, "invalid_phone_number", AGENT_TYPE, flagged=True)


@safe_clean_row(AGENT_TYPE)
def clean_email_value(raw_value: str) -> AgentResult:
    value = raw_value.strip()
    try:
        result = validate_email(value, check_deliverability=False)
        return AgentResult(value, result.normalized, CONFIDENCE_HIGH, "valid_email", AGENT_TYPE)
    except (EmailNotValidError, EmailSyntaxError):
        pass

    # One cleanup pass (the "extra whitespace" dirty case is already
    # stripped above; this also lowercases before retrying) before giving up.
    retried = value.strip().lower()
    if retried != value:
        try:
            result = validate_email(retried, check_deliverability=False)
            return AgentResult(value, result.normalized, CONFIDENCE_MEDIUM, "cleaned_then_validated", AGENT_TYPE)
        except (EmailNotValidError, EmailSyntaxError):
            pass

    return AgentResult(value, None, 0.0, "invalid_email", AGENT_TYPE, flagged=True)


def clean_column(
    series: pd.Series, column_type: str, feedback_map: dict[str, str] | None = None
) -> list[AgentResult]:
    if column_type not in ("phone", "email"):
        raise ValueError(f"ContactAgent cannot handle column_type={column_type!r}")
    cleaner = clean_phone_value if column_type == "phone" else clean_email_value

    results: list[AgentResult] = []
    for v in series.tolist():
        if pd.isna(v) or str(v).strip() == "":
            results.append(AgentResult(v, None, 0.0, "missing_value", AGENT_TYPE, flagged=True))
            continue
        feedback_result = check_feedback(str(v), feedback_map, AGENT_TYPE)
        if feedback_result is not None:
            results.append(feedback_result)
            continue
        results.append(cleaner(str(v)))
    return results
