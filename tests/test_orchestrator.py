import pandas as pd
import pytest

from app.orchestrator.column_detection import detect_column_type
from app.orchestrator.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    InvalidFileTypeError,
    NoColumnsError,
)
from app.orchestrator.file_validation import validate_and_load_upload
from app.orchestrator.orchestrator import build_routing_plan
from app.synthetic.generator import generate_dataset

DEFAULT_ALLOWED = (".csv",)


# --- Column type detection on the real synthetic dataset -------------------


def test_routing_plan_matches_expected_agents_on_synthetic_dataset():
    df, _, _ = generate_dataset(seed=42, n_customers=200)
    plan = build_routing_plan(df)

    assert plan.column_agents["email"] == "ContactAgent"
    assert plan.column_agents["phone"] == "ContactAgent"
    assert plan.column_agents["signup_date"] == "DateAgent"
    assert plan.column_agents["order_date"] == "DateAgent"
    assert plan.column_agents["address"] == "AddressAgent"
    assert plan.column_agents["order_amount"] == "CurrencyAgent"
    assert plan.column_agents["customer_age"] == "NumericAgent"

    # No agent owns these — orchestrator must leave them untouched.
    assert plan.column_agents["customer_id"] == "unclassified"
    assert plan.column_agents["name"] == "unclassified"
    assert plan.column_agents["category"] == "unclassified"
    assert plan.column_agents["customer_segment"] == "unclassified"
    assert plan.column_agents["currency_hint"] == "unclassified"


# --- The specific "don't force-route on header name" adversarial case ------


def test_date_like_header_with_free_text_values_is_not_routed_to_date_agent():
    notes = pd.Series([
        "Customer called about a delayed shipment and asked for a refund.",
        "Left a voicemail, will follow up next week regarding the order.",
        "Requested gift wrapping for the December order, see attached note.",
        "Complained about packaging damage on arrival, replacement sent.",
        "Asked to change the delivery date but no new date was confirmed.",
    ] * 5)
    result = detect_column_type(notes, column_name="order_date_notes")
    assert result.agent_type == "unclassified"
    assert result.detected_type != "date"


def test_clean_date_column_is_routed_to_date_agent_regardless_of_header():
    dates = pd.Series(["2024-01-15", "2024-02-20", "2024-03-05", "2024-04-10"] * 5)
    result = detect_column_type(dates, column_name="mystery_column")
    assert result.detected_type == "date"
    assert result.agent_type == "DateAgent"


def test_numeric_id_column_without_age_header_is_not_routed_to_numeric_agent():
    ids = pd.Series([str(i) for i in range(1, 51)])
    result = detect_column_type(ids, column_name="customer_id")
    assert result.agent_type == "unclassified"


def test_numeric_column_with_age_header_is_routed_to_numeric_agent():
    ages = pd.Series(["25", "34", " 41 ", "thirty-five", "60"] * 5)
    result = detect_column_type(ages, column_name="customer_age")
    assert result.detected_type == "numeric_age"
    assert result.agent_type == "NumericAgent"


def test_all_null_column_is_unclassified_without_crashing():
    empty = pd.Series([None, None, None])
    result = detect_column_type(empty, column_name="order_date")
    assert result.agent_type == "unclassified"
    assert result.sample_size == 0


# --- File validation: adversarial upload scenarios --------------------------


def test_empty_file_is_rejected_cleanly():
    with pytest.raises(EmptyFileError):
        validate_and_load_upload(b"", filename="empty.csv", max_bytes=10_000, allowed_extensions=DEFAULT_ALLOWED)


def test_header_only_file_is_handled_gracefully_not_an_error():
    raw = b"customer_id,name,email\n"
    result = validate_and_load_upload(raw, filename="headers.csv", max_bytes=10_000, allowed_extensions=DEFAULT_ALLOWED)
    assert list(result.df.columns) == ["customer_id", "name", "email"]
    assert len(result.df) == 0
    assert any("zero data rows" in w for w in result.warnings)


def test_single_column_file_is_handled():
    raw = b"name\nAlice\nBob\n"
    result = validate_and_load_upload(raw, filename="single.csv", max_bytes=10_000, allowed_extensions=DEFAULT_ALLOWED)
    assert list(result.df.columns) == ["name"]
    assert len(result.df) == 2


def test_column_with_100_percent_missing_values_does_not_crash():
    raw = b"name,notes\nAlice,\nBob,\nCarol,\n"
    result = validate_and_load_upload(raw, filename="missing.csv", max_bytes=10_000, allowed_extensions=DEFAULT_ALLOWED)
    plan = build_routing_plan(result.df)
    assert plan.column_agents["notes"] == "unclassified"


def test_duplicate_column_names_are_warned_not_silently_overwritten():
    raw = b"name,name,email\nAlice,Alicia,a@example.com\n"
    result = validate_and_load_upload(raw, filename="dupes.csv", max_bytes=10_000, allowed_extensions=DEFAULT_ALLOWED)
    assert any("Duplicate column" in w for w in result.warnings)
    # pandas disambiguates rather than dropping the second column's data
    assert "name.1" in result.df.columns
    assert result.df["name"].iloc[0] == "Alice"
    assert result.df["name.1"].iloc[0] == "Alicia"


def test_file_over_size_limit_is_rejected_before_parsing():
    raw = b"a,b\n" + b"1,2\n" * 1000
    with pytest.raises(FileTooLargeError):
        validate_and_load_upload(raw, filename="big.csv", max_bytes=10, allowed_extensions=DEFAULT_ALLOWED)


def test_file_exactly_at_size_limit_is_accepted_one_byte_over_is_rejected():
    raw = b"a,b\n1,2\n"
    validate_and_load_upload(raw, filename="exact.csv", max_bytes=len(raw), allowed_extensions=DEFAULT_ALLOWED)
    with pytest.raises(FileTooLargeError):
        validate_and_load_upload(raw, filename="over.csv", max_bytes=len(raw) - 1, allowed_extensions=DEFAULT_ALLOWED)


def test_image_renamed_to_csv_is_rejected_with_clear_message_not_parser_crash():
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    with pytest.raises(InvalidFileTypeError):
        validate_and_load_upload(png_bytes, filename="photo.csv", max_bytes=10_000, allowed_extensions=DEFAULT_ALLOWED)


def test_wrong_encoding_is_recovered_via_detection_not_a_crash():
    # Latin-1 bytes that are invalid UTF-8 (e.g. "café" with a raw 0xE9).
    raw = "name\nCafé René\n".encode("latin-1")
    result = validate_and_load_upload(raw, filename="latin1.csv", max_bytes=10_000, allowed_extensions=DEFAULT_ALLOWED)
    assert result.encoding_used != "utf-8"
    assert "René" in result.df["name"].iloc[0] or "Ren" in result.df["name"].iloc[0]


def test_wrong_extension_is_rejected():
    with pytest.raises(InvalidFileTypeError):
        validate_and_load_upload(b"a,b\n1,2\n", filename="data.txt", max_bytes=10_000, allowed_extensions=DEFAULT_ALLOWED)


def test_no_columns_file_is_rejected():
    with pytest.raises((EmptyFileError, NoColumnsError)):
        validate_and_load_upload(b"\n\n\n", filename="blank.csv", max_bytes=10_000, allowed_extensions=DEFAULT_ALLOWED)
