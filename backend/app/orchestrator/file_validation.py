"""Everything that must be checked about an uploaded file *before* it becomes
a pandas DataFrame the rest of the pipeline can trust. Every check here
answers one adversarial-testing item from the Phase 3 roadmap section.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

import chardet
import pandas as pd

from app.orchestrator.exceptions import (
    CSVParseError,
    EmptyFileError,
    EncodingDetectionError,
    FileTooLargeError,
    InvalidFileTypeError,
    NoColumnsError,
)

# Magic bytes for common formats someone might rename to `.csv`. Checked
# before any attempt to decode/parse, so a disguised binary file gets a
# clear rejection instead of a raw parser exception.
_BINARY_SIGNATURES: dict[bytes, str] = {
    b"\x89PNG\r\n\x1a\n": "PNG image",
    b"\xff\xd8\xff": "JPEG image",
    b"GIF87a": "GIF image",
    b"GIF89a": "GIF image",
    b"%PDF-": "PDF document",
    b"PK\x03\x04": "ZIP-based file (e.g. .xlsx/.docx/.zip)",
    b"\x1f\x8b": "gzip archive",
}


@dataclass
class ValidatedUpload:
    df: pd.DataFrame
    encoding_used: str
    warnings: list[str] = field(default_factory=list)


def validate_file_size(raw_bytes: bytes, max_bytes: int) -> None:
    """Checked on the raw bytes/size before any parsing — a too-large file
    is rejected before it's ever loaded into a DataFrame."""
    if len(raw_bytes) > max_bytes:
        raise FileTooLargeError(
            f"File is {len(raw_bytes) / (1024 * 1024):.1f} MB, which exceeds the "
            f"{max_bytes / (1024 * 1024):.0f} MB limit."
        )


def validate_extension(filename: str, allowed_extensions: tuple[str, ...]) -> None:
    lowered = filename.lower()
    if not any(lowered.endswith(ext) for ext in allowed_extensions):
        raise InvalidFileTypeError(
            f"'{filename}' does not have an allowed extension ({', '.join(allowed_extensions)})."
        )


def reject_disguised_binary(raw_bytes: bytes) -> None:
    """Catches "an image renamed to .csv" — checked by content, not filename."""
    head = raw_bytes[:16]
    for signature, kind in _BINARY_SIGNATURES.items():
        if head.startswith(signature):
            raise InvalidFileTypeError(
                f"File content looks like a {kind}, not a CSV, despite its extension."
            )
    # A NUL byte essentially never appears in a legitimate text/CSV file and
    # is common in arbitrary binary formats not covered by a known signature.
    if b"\x00" in raw_bytes[:8192]:
        raise InvalidFileTypeError("File content looks like binary data, not a CSV.")


def decode_with_fallback(raw_bytes: bytes) -> tuple[str, str]:
    """Tries UTF-8 first (the expected case); if that fails, runs encoding
    detection (chardet) before giving up — never crashes on a Latin-1 file
    uploaded without a BOM."""
    try:
        return raw_bytes.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    detected = chardet.detect(raw_bytes)
    encoding = detected.get("encoding")
    if not encoding:
        raise EncodingDetectionError("Could not detect the file's text encoding.")
    try:
        return raw_bytes.decode(encoding), encoding
    except (UnicodeDecodeError, LookupError) as exc:
        raise EncodingDetectionError(
            f"File encoding could not be reliably decoded (best guess was '{encoding}')."
        ) from exc


def _detect_duplicate_headers(text: str) -> list[str]:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return []
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for col in header:
        seen[col] = seen.get(col, 0) + 1
        if seen[col] == 2:
            duplicates.append(col)
    return duplicates


def parse_csv_text(text: str) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []

    if not text.strip():
        raise EmptyFileError("File is empty.")

    duplicate_headers = _detect_duplicate_headers(text)
    if duplicate_headers:
        warnings.append(
            "Duplicate column names detected in header row "
            f"({', '.join(duplicate_headers)}); pandas has disambiguated them "
            "with numeric suffixes (e.g. 'name.1') rather than silently "
            "overwriting one column's data with another's."
        )

    try:
        df = pd.read_csv(io.StringIO(text))
    except pd.errors.EmptyDataError as exc:
        raise EmptyFileError("File has no header row / no parseable content.") from exc
    except pd.errors.ParserError as exc:
        raise CSVParseError(f"File could not be parsed as CSV: {exc}") from exc

    if len(df.columns) == 0:
        raise NoColumnsError("File has no columns.")

    if len(df) == 0:
        warnings.append("File has a valid header but zero data rows.")

    return df, warnings


def validate_and_load_upload(
    raw_bytes: bytes, *, filename: str, max_bytes: int, allowed_extensions: tuple[str, ...]
) -> ValidatedUpload:
    """Runs every adversarial-input check, in order, before returning a
    trustworthy DataFrame. Raises a UploadValidationError subclass with a
    clear message on any failure — never lets a raw parser/codec exception
    escape to the caller."""
    if len(raw_bytes) == 0:
        raise EmptyFileError("File is empty.")

    validate_file_size(raw_bytes, max_bytes)
    validate_extension(filename, allowed_extensions)
    reject_disguised_binary(raw_bytes)

    text, encoding_used = decode_with_fallback(raw_bytes)
    df, warnings = parse_csv_text(text)

    return ValidatedUpload(df=df, encoding_used=encoding_used, warnings=warnings)
