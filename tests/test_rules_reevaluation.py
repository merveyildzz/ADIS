import pandas as pd
import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db import repository as repo
from app.db.base import Base, create_app_engine
from app.db.models import RuleTargetKind, UploadStatus
from app.orchestrator.orchestrator import build_routing_plan
from app.pipeline import run_cleaning_pipeline
from app.rules.reevaluation import reevaluate_rule_against_existing_uploads


@pytest.fixture()
def session(tmp_path):
    engine = create_app_engine(Settings(database_url=f"sqlite:///{tmp_path}/test.db"))
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    yield db
    db.close()
    engine.dispose()


def _upload_and_clean(session, df, filename="test.csv"):
    upload = repo.create_raw_upload(session, filename=filename, row_count=len(df))
    plan = build_routing_plan(df, upload_id=upload.upload_id)
    run_cleaning_pipeline(session, upload_id=upload.upload_id, df=df, routing_plan=plan)
    return upload


def test_new_rule_is_retroactively_evaluated_against_a_preexisting_upload(session):
    df = pd.DataFrame({"customer_age": ["30", "-5", "45"]})
    _upload_and_clean(session, df)

    rule = repo.create_custom_rule(
        session, name="age not negative", target_kind="column_name", target_value="customer_age",
        condition_operator="gte", condition_value="0",
    )
    written = reevaluate_rule_against_existing_uploads(session, rule=rule)
    assert written == 1

    upload = repo.list_raw_uploads(session)[0]
    violations = repo.list_rule_violations_for_upload(session, upload_id=upload.upload_id)
    assert len(violations) == 1


def test_retroactive_evaluation_covers_every_completed_upload(session):
    df = pd.DataFrame({"customer_age": ["-1"]})
    _upload_and_clean(session, df, filename="a.csv")
    _upload_and_clean(session, df, filename="b.csv")

    rule = repo.create_custom_rule(
        session, name="age not negative", target_kind="column_name", target_value="customer_age",
        condition_operator="gte", condition_value="0",
    )
    written = reevaluate_rule_against_existing_uploads(session, rule=rule)
    assert written == 2


def test_retroactive_evaluation_of_a_rule_matching_nothing_writes_zero_and_does_not_error(session):
    df = pd.DataFrame({"customer_age": ["30"]})
    _upload_and_clean(session, df)

    rule = repo.create_custom_rule(
        session, name="unrelated column", target_kind="column_name", target_value="does_not_exist",
        condition_operator="not_null", condition_value=None,
    )
    written = reevaluate_rule_against_existing_uploads(session, rule=rule)
    assert written == 0


def test_retroactive_evaluation_skips_uploads_that_never_completed(session):
    upload = repo.create_raw_upload(session, filename="pending.csv")
    assert upload.status == UploadStatus.PENDING

    rule = repo.create_custom_rule(
        session, name="whatever", target_kind="detected_type", target_value="numeric_age",
        condition_operator="gte", condition_value="0",
    )
    written = reevaluate_rule_against_existing_uploads(session, rule=rule)
    assert written == 0


def test_retroactive_evaluation_via_detected_type_applies_regardless_of_column_name(session):
    df = pd.DataFrame({"applicant_age": ["-3"]})
    _upload_and_clean(session, df)

    rule = repo.create_custom_rule(
        session, name="no negative ages anywhere", target_kind="detected_type", target_value="numeric_age",
        condition_operator="gte", condition_value="0",
    )
    written = reevaluate_rule_against_existing_uploads(session, rule=rule)
    assert written == 1
