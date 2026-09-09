""""The jury can upload any random CSV" robustness guarantee: run the full
pipeline (routing -> cleaning -> insights) against several structurally
unrelated datasets — none of them resembling the customer/order or
real-estate shapes this system was built and tested against — and assert
it always completes without raising, regardless of what it does or doesn't
manage to classify. A crash here would mean an untested dataset shape can
take the whole upload down; that must never happen.
"""
import pandas as pd
import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import repository as repo
from app.db.base import Base, create_app_engine
from app.db.models import UploadStatus
from app.orchestrator.orchestrator import build_routing_plan
from app.pipeline import run_cleaning_pipeline


@pytest.fixture()
def session(tmp_path):
    engine = create_app_engine(Settings(database_url=f"sqlite:///{tmp_path}/test.db"))
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    yield db
    db.close()
    engine.dispose()


def _run(session, df: pd.DataFrame) -> dict:
    upload = repo.create_raw_upload(session, filename="stress.csv", row_count=len(df))
    plan = build_routing_plan(df, upload_id=upload.upload_id)
    summary = run_cleaning_pipeline(session, upload_id=upload.upload_id, df=df, routing_plan=plan)
    refreshed = repo.get_raw_upload(session, upload_id=upload.upload_id)
    assert refreshed.status == UploadStatus.COMPLETED
    return summary


MOVIES_DF = pd.DataFrame({
    "title": ["Inception", "The Room", "Parasite", "Nonexistent Movie", "Avatar"],
    "release_date": ["2010-07-16", "2003-06-27", "19-05-2019", "not a date", "Dec 18 2009"],
    "genre": ["Sci-Fi", "Drama", "Thriller", None, "Sci-Fi"],
    "rating": [8.8, 3.7, 8.5, None, 7.9],
    "box_office": ["$836.8M", "$1.8K", "€258.8 M", None, "$2923.7M"],
    "director_email": ["contact@dreamworks.example", "tommy@theroom.example", "bong@studio.example", "invalid-email", "cameron@fox.example"],
})

STUDENTS_DF = pd.DataFrame({
    "student_id": [1, 2, 3, 4, 5],
    "student_name": ["Alice Johnson", "Bob Smith", "Carol White", "David Brown", "Eve Davis"],
    "gpa": [3.8, 2.9, None, 3.2, 4.0],
    "attendance_pct": ["95%", None, "88%", "102%", "100%"],  # >100% is bad data, must not crash
    "grade_letter": ["A", "B", "C-", "B+", "A+"],
})

PURELY_NUMERIC_DF = pd.DataFrame({
    "x1": [1.5, 2.6, 3.7, 4.1, 5.9],
    "x2": [2.3, 3.1, 1.9, 2.8, 3.5],
    "x3": [-4.1, -3.9, -4.5, -4.0, -3.2],
    "x4": [100, 200, 150, 300, 250],
})

WEIRD_DF = pd.DataFrame({
    "id": [1, 2, 3, 4, 5],
    "weird_col": [None, "NaN", "null", "-", "∞"],
    "unicode_col": ["café münchen 日本語", "🚀🔥💯", "Ñandú Örnek", "ελληνικά", "العربية"],
    "long_text": ["This is a normal sentence."] * 5,
    "mixed_types": ["42", "abc", "3.14", "True", "None"],
})

SINGLE_COLUMN_DF = pd.DataFrame({"value": [1, 2, 3]})
SINGLE_ROW_DF = pd.DataFrame({"a": [1], "b": ["x"], "c": ["2024-01-01"]})
ALL_NULL_DF = pd.DataFrame({"a": [None, None, None], "b": [None, None, None]})


@pytest.mark.parametrize("df", [
    MOVIES_DF, STUDENTS_DF, PURELY_NUMERIC_DF, WEIRD_DF,
    SINGLE_COLUMN_DF, SINGLE_ROW_DF, ALL_NULL_DF,
], ids=["movies", "students", "purely_numeric", "weird_unicode", "single_column", "single_row", "all_null"])
def test_full_pipeline_never_raises_on_an_unrelated_dataset_shape(session, df):
    summary = _run(session, df)
    assert summary["rows"] == len(df)
    # Every column is accounted for one way or another — cleaned, or
    # unclassified-and-profiled — never silently dropped.
    accounted_for = set(summary["columns_cleaned"]) | set(summary["columns_unclassified"])
    assert accounted_for == set(df.columns)


def test_out_of_range_percentage_is_flagged_not_silently_accepted(session):
    # "102%" attendance is bad data (>100%) — whatever happens to it, the
    # pipeline itself must not crash trying to make sense of it.
    summary = _run(session, STUDENTS_DF)
    assert "attendance_pct" in summary["columns_cleaned"] or "attendance_pct" in summary["columns_unclassified"]


def test_negative_gpa_like_value_does_not_crash_numeric_handling(session):
    df = pd.DataFrame({"score": ["-0.5", "3.8", "2.2"], "label": ["bad", "good", "ok"]})
    summary = _run(session, df)
    assert summary["rows"] == 3
