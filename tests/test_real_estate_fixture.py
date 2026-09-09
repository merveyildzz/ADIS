"""Permanent regression fixture (requirement 4, part 2): a structurally
different dataset (Indian real-estate listings) from the original synthetic
Turkish customer/order dataset, proving column routing generalizes by
content rather than by the specific column names/shapes it was built
against. Always part of the run — not marked slow/skip.
"""
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import repository as repo
from app.db.base import Base, create_app_engine
from app.orchestrator.column_detection import detect_column_type
from app.orchestrator.orchestrator import build_routing_plan
from app.pipeline import run_cleaning_pipeline

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "real_estate_sample.csv"


@pytest.fixture()
def real_estate_df():
    return pd.read_csv(FIXTURE_PATH)


@pytest.fixture()
def session(tmp_path):
    engine = create_app_engine(Settings(database_url=f"sqlite:///{tmp_path}/test.db"))
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    yield db
    db.close()
    engine.dispose()


def test_flat_price_routes_to_quantity_agent_with_crore_and_lacs_multipliers(real_estate_df):
    result = detect_column_type(real_estate_df["Flat_Price"], "Flat_Price")
    assert result.detected_type == "quantity"
    assert result.agent_type == "QuantityAgent"


def test_price_per_sqft_routes_to_quantity_agent_with_combined_currency_and_unit(real_estate_df):
    result = detect_column_type(real_estate_df["Price_per_sq.ft"], "Price_per_sq.ft")
    assert result.detected_type == "quantity"
    assert result.agent_type == "QuantityAgent"


def test_total_sqft_routes_to_quantity_agent_without_any_currency_in_the_values(real_estate_df):
    result = detect_column_type(real_estate_df["Total_Sq.ft"], "Total_Sq.ft")
    assert result.detected_type == "quantity"
    assert result.agent_type == "QuantityAgent"


def test_house_type_is_categorical_and_profiled_not_routed_to_an_agent(real_estate_df):
    plan = build_routing_plan(real_estate_df)
    assert plan.column_results["HOUSE_TYPE"].detected_type == "categorical"
    assert plan.column_agents["HOUSE_TYPE"] == "unclassified"
    assert plan.column_results["HOUSE_TYPE"].profile is not None
    assert plan.column_results["HOUSE_TYPE"].profile.unique_count == 4


def test_owner_name_free_text_is_unclassified_and_profiled(real_estate_df):
    plan = build_routing_plan(real_estate_df)
    assert plan.column_results["Owner_name"].detected_type is None
    assert plan.column_agents["Owner_name"] == "unclassified"
    assert plan.column_results["Owner_name"].profile is not None
    assert plan.column_results["Owner_name"].profile.unique_count == len(real_estate_df)


def test_full_pipeline_run_on_real_estate_fixture_succeeds_with_no_exceptions(session, real_estate_df):
    upload = repo.create_raw_upload(session, filename="real_estate_sample.csv")
    plan = build_routing_plan(real_estate_df, upload_id=upload.upload_id)
    summary = run_cleaning_pipeline(session, upload_id=upload.upload_id, df=real_estate_df, routing_plan=plan)

    for col in ("Flat_Price", "EMI_Starts", "Total_Sq.ft", "Price_per_sq.ft"):
        assert col in summary["columns_cleaned"]
    assert "HOUSE_TYPE" in summary["columns_profiled"]
    assert "Owner_name" in summary["columns_profiled"]
    assert summary["columns_profiled"]["HOUSE_TYPE"]["unique_count"] == 4

    flat_price_records = repo.get_cleaned_records(
        session, upload_id=upload.upload_id, column_name="Flat_Price", limit=100,
    )
    assert len(flat_price_records) == len(real_estate_df)
    first_crore_record = next(r for r in flat_price_records if r.original_value == "₹8.5 Cr")
    assert first_crore_record.cleaned_value == "85000000.00"


def test_insights_compute_without_crashing_on_quantity_typed_columns(session, real_estate_df):
    # Regression: quantity-typed columns (Flat_Price, EMI_Starts, etc.) must
    # reach the insight layer as numeric dtype, not the string dtype they'd
    # be left as without build_analysis_dataframe's "quantity" case — that
    # gap crashed insight computation entirely on a real house-price dataset.
    upload = repo.create_raw_upload(session, filename="real_estate_sample.csv")
    plan = build_routing_plan(real_estate_df, upload_id=upload.upload_id)
    run_cleaning_pipeline(session, upload_id=upload.upload_id, df=real_estate_df, routing_plan=plan)

    refreshed = repo.get_raw_upload(session, upload_id=upload.upload_id)
    assert refreshed.insights_json is not None
