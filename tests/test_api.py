import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

SAMPLE_CSV = (
    b"customer_id,name,order_date,order_amount,customer_age\n"
    b"1,Alice,2024-01-15,$120.50,34\n"
    b"2,Bob,2024-02-20,1.500 TL, thirty-five \n"
    b'3,Carol,not-a-date,"85,50",\n'
)


@pytest.fixture()
def client(tmp_path):
    from app.config import Settings
    from app.db.base import Base, create_app_engine, get_db
    from app.main import app

    test_engine = create_app_engine(Settings(database_url=f"sqlite:///{tmp_path}/test.db"))
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()


def _upload_sample(client) -> dict:
    response = client.post(
        "/api/uploads", files={"file": ("test.csv", SAMPLE_CSV, "text/csv")}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_runs_pipeline_and_returns_summary(client):
    body = _upload_sample(client)
    assert body["upload"]["status"] == "completed"
    assert body["upload"]["row_count"] == 3
    assert "order_date" in body["columns_cleaned"]
    assert "customer_id" in body["columns_unclassified"]


def test_upload_rejects_empty_file(client):
    response = client.post("/api/uploads", files={"file": ("empty.csv", b"", "text/csv")})
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_upload_rejects_disguised_binary(client):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    response = client.post("/api/uploads", files={"file": ("photo.csv", png, "text/csv")})
    assert response.status_code == 400


def test_get_uploads_list(client):
    _upload_sample(client)
    response = client.get("/api/uploads")
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_upload_not_found_returns_404(client):
    response = client.get("/api/uploads/999")
    assert response.status_code == 404


def test_cleaned_records_paginated_and_filterable(client):
    body = _upload_sample(client)
    upload_id = body["upload"]["upload_id"]

    response = client.get(f"/api/uploads/{upload_id}/cleaned-records", params={"column_name": "order_date"})
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 3
    assert len(page["items"]) == 3
    assert all(item["column_name"] == "order_date" for item in page["items"])


def test_cleaned_records_confidence_filter_finds_low_confidence_cells(client):
    body = _upload_sample(client)
    upload_id = body["upload"]["upload_id"]

    response = client.get(
        f"/api/uploads/{upload_id}/cleaned-records", params={"max_confidence": 60}
    )
    page = response.json()
    assert page["total"] >= 1
    assert all(item["confidence_score"] < 60 for item in page["items"])


def test_cleaned_records_empty_state_when_nothing_below_threshold(client):
    body = _upload_sample(client)
    upload_id = body["upload"]["upload_id"]

    # Nothing should ever score below 0 — this must be an empty, clearly
    # non-error response (Phase 5 "needs review" empty-state requirement).
    response = client.get(
        f"/api/uploads/{upload_id}/cleaned-records", params={"max_confidence": 0}
    )
    assert response.status_code == 200
    page = response.json()
    assert page["items"] == []
    assert page["total"] == 0


def test_cleaned_records_for_missing_upload_is_404(client):
    response = client.get("/api/uploads/999/cleaned-records")
    assert response.status_code == 404


def test_pagination_limit_is_capped_server_side(client):
    body = _upload_sample(client)
    upload_id = body["upload"]["upload_id"]
    response = client.get(f"/api/uploads/{upload_id}/cleaned-records", params={"limit": 999999})
    assert response.status_code == 200
    from app.config import get_settings
    assert response.json()["limit"] == get_settings().max_page_size


def test_lineage_drilldown_returns_full_history(client):
    body = _upload_sample(client)
    upload_id = body["upload"]["upload_id"]
    records = client.get(f"/api/uploads/{upload_id}/cleaned-records", params={"column_name": "customer_age"}).json()
    spelled_out = next(r for r in records["items"] if r["original_value"].strip() == "thirty-five")

    response = client.get(f"/api/uploads/{upload_id}/records/{spelled_out['record_id']}/lineage")
    assert response.status_code == 200
    lineage = response.json()
    assert lineage["has_lineage"] is True
    assert len(lineage["history"]) == 1
    assert lineage["history"][0]["agent_name"] == "NumericAgent"
    assert lineage["record"]["cleaned_value"] == "35"


def test_lineage_for_nonexistent_record_is_404(client):
    body = _upload_sample(client)
    upload_id = body["upload"]["upload_id"]
    response = client.get(f"/api/uploads/{upload_id}/records/999999/lineage")
    assert response.status_code == 404


def test_lineage_for_record_belonging_to_different_upload_is_404(client):
    body1 = _upload_sample(client)
    body2 = _upload_sample(client)
    upload1_id = body1["upload"]["upload_id"]
    records2 = client.get(
        f"/api/uploads/{body2['upload']['upload_id']}/cleaned-records", params={"limit": 1}
    ).json()
    other_record_id = records2["items"][0]["record_id"]

    response = client.get(f"/api/uploads/{upload1_id}/records/{other_record_id}/lineage")
    assert response.status_code == 404


# --- Phase 6: corrections / feedback loop -------------------------------------------------


def test_submit_correction_updates_the_cell(client):
    body = _upload_sample(client)
    upload_id = body["upload"]["upload_id"]
    records = client.get(f"/api/uploads/{upload_id}/cleaned-records", params={"column_name": "order_date"}).json()
    record_id = records["items"][0]["record_id"]

    response = client.post(
        f"/api/uploads/{upload_id}/records/{record_id}/correction", json={"corrected_value": "2024-01-15"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["cleaned_value"] == "2024-01-15"
    assert body["confidence_score"] == 100.0


def test_correction_appears_in_lineage_history(client):
    body = _upload_sample(client)
    upload_id = body["upload"]["upload_id"]
    records = client.get(f"/api/uploads/{upload_id}/cleaned-records", params={"column_name": "order_date"}).json()
    record_id = records["items"][0]["record_id"]

    client.post(f"/api/uploads/{upload_id}/records/{record_id}/correction", json={"corrected_value": "2024-01-15"})

    lineage = client.get(f"/api/uploads/{upload_id}/records/{record_id}/lineage").json()
    assert any(entry["agent_name"] == "UserFeedback" for entry in lineage["history"])


def test_correction_for_nonexistent_record_is_404(client):
    body = _upload_sample(client)
    upload_id = body["upload"]["upload_id"]
    response = client.post(
        f"/api/uploads/{upload_id}/records/999999/correction", json={"corrected_value": "x"}
    )
    assert response.status_code == 404


def test_correction_is_reused_by_a_later_upload_with_the_same_raw_value(client):
    # Correct the ambiguous "not-a-date" -> pick a concrete value, then
    # upload the same raw value again and confirm it's resolved from
    # feedback instead of re-guessing.
    body1 = _upload_sample(client)
    upload1_id = body1["upload"]["upload_id"]
    records = client.get(f"/api/uploads/{upload1_id}/cleaned-records", params={"column_name": "order_date"}).json()
    ambiguous_record = next(r for r in records["items"] if r["original_value"] == "not-a-date")
    client.post(
        f"/api/uploads/{upload1_id}/records/{ambiguous_record['record_id']}/correction",
        json={"corrected_value": "2024-05-05"},
    )

    body2 = _upload_sample(client)
    upload2_id = body2["upload"]["upload_id"]
    records2 = client.get(f"/api/uploads/{upload2_id}/cleaned-records", params={"column_name": "order_date"}).json()
    reused_record = next(r for r in records2["items"] if r["original_value"] == "not-a-date")
    assert reused_record["cleaned_value"] == "2024-05-05"
    assert reused_record["confidence_score"] == 95.0


# --- Phase 7: insights -------------------------------------------------


def test_get_insights_for_nonexistent_upload_is_404(client):
    response = client.get("/api/uploads/999/insights")
    assert response.status_code == 404


def test_get_insights_returns_available_with_deterministic_structure(client):
    body = _upload_sample(client)
    upload_id = body["upload"]["upload_id"]
    response = client.get(f"/api/uploads/{upload_id}/insights")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert "correlations" in payload and "trends" in payload and "anomalies" in payload
    assert isinstance(payload["warnings"], list)


def test_get_insights_finds_correlation_and_causation_disclaimer_end_to_end(client):
    import random

    rng = random.Random(0)
    rows = [b"customer_id,order_date,order_amount,customer_age"]
    for i in range(60):
        age = rng.randint(18, 80)
        amount = age * 4 + rng.gauss(0, 8)
        rows.append(f"{i},2024-01-{(i % 28) + 1:02d},${amount:.2f},{age}".encode())
    csv_bytes = b"\n".join(rows) + b"\n"

    response = client.post("/api/uploads", files={"file": ("bulk.csv", csv_bytes, "text/csv")})
    assert response.status_code == 200
    upload_id = response.json()["upload"]["upload_id"]

    insights = client.get(f"/api/uploads/{upload_id}/insights").json()
    assert insights["available"] is True
    assert len(insights["correlations"]) >= 1
    assert all("Correlation does not imply causation." in c["narrative"] for c in insights["correlations"])
    assert "correlation_matrix" in insights["charts"]
    assert "correlation_scatter" in insights["charts"]


# --- Phase 9: client config -------------------------------------------------


def test_get_config_matches_backend_settings(client):
    from app.config import get_settings

    response = client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    settings = get_settings()
    assert body["max_upload_size_mb"] == settings.max_upload_size_mb
    assert body["allowed_file_extensions"] == list(settings.allowed_file_extensions)
