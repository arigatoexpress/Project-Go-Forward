"""Guards for the tho-leads-bq Cloud Run Job (scripts/sync_leads_to_bigquery.py)."""

from pathlib import Path

import pytest

from scripts import sync_leads_to_bigquery as sync

ROOT = Path(__file__).resolve().parents[1]
JOB_DIR = ROOT / "jobs" / "leads_bq_sync"


class _FakeBQ:
    def __init__(self, exists: bool):
        self.exists = exists
        self.created = []

    def get_dataset(self, ref):
        if not self.exists:
            from google.api_core.exceptions import NotFound

            raise NotFound(f"dataset {ref} not found")
        return {"ref": ref}

    def create_dataset(self, ds, exists_ok=False):
        self.created.append((ds, exists_ok))
        return ds


def test_existing_dataset_is_reused_without_a_create_call():
    pytest.importorskip("google.api_core.exceptions")
    bq = _FakeBQ(exists=True)
    assert sync.ensure_dataset(bq, "tho-ai-agent.tho_analytics", "US") == {
        "ref": "tho-ai-agent.tho_analytics"
    }
    assert bq.created == []


def test_missing_dataset_is_created_in_the_requested_location():
    pytest.importorskip("google.api_core.exceptions")
    if not hasattr(sync.bigquery, "Dataset"):
        pytest.skip("google-cloud-bigquery not installed")
    bq = _FakeBQ(exists=False)
    sync.ensure_dataset(bq, "tho-ai-agent.tho_analytics", "US")
    assert len(bq.created) == 1
    ds, exists_ok = bq.created[0]
    assert exists_ok is True
    assert ds.location == "US"
    assert "PII-FREE" in ds.description


def test_job_image_ships_only_the_sync_script_as_non_root():
    dockerfile = (JOB_DIR / "Dockerfile").read_text()
    copies = [line for line in dockerfile.splitlines() if line.startswith("COPY")]
    assert copies == [
        "COPY jobs/leads_bq_sync/requirements.txt /app/requirements.txt",
        "COPY scripts/sync_leads_to_bigquery.py /app/sync_leads_to_bigquery.py",
    ]
    assert "USER job" in dockerfile
    assert 'ENTRYPOINT ["python", "/app/sync_leads_to_bigquery.py"]' in dockerfile


def test_job_requirements_are_pinned_and_match_the_app_firestore_pin():
    job_reqs = {
        line.split("==")[0]: line.split("==")[1]
        for line in (JOB_DIR / "requirements.txt").read_text().splitlines()
        if line and not line.startswith("#")
    }
    assert set(job_reqs) == {"google-cloud-bigquery", "google-cloud-firestore"}
    app_reqs = (ROOT / "requirements.txt").read_text()
    assert f"google-cloud-firestore=={job_reqs['google-cloud-firestore']}" in app_reqs
