"""Test configuration: every test gets its own temporary data directory and database."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Must be set before the app (and its cached settings) are imported.
_BOOT_DIR = tempfile.mkdtemp(prefix="processlens_test_boot_")
os.environ.setdefault("PROCESSLENS_DATA_DIR", _BOOT_DIR)
os.environ["PROCESSLENS_ALLOWED_HOSTS"] = "testserver,localhost,127.0.0.1"
os.environ["PROCESSLENS_SECRET_KEY"] = "test-secret-key"
os.environ["PROCESSLENS_LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas.imports import ColumnMapping  # noqa: E402
from app.security import csrf_token  # noqa: E402
from app.services import jobs  # noqa: E402
from app.services.cache import cache  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
HAND_CHECK = FIXTURES / "hand_check.csv"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    settings = get_settings()
    old = (settings.data_dir, settings.database_url)
    settings.data_dir = tmp_path / "data"
    settings.database_url = None
    cache.clear()
    yield settings.data_dir
    settings.data_dir, settings.database_url = old
    cache.clear()


@pytest.fixture
def client(data_dir):
    with TestClient(app) as c:
        c.headers["X-CSRF-Token"] = csrf_token()
        yield c


def upload(client: TestClient, content: bytes | str, name: str = "log.csv") -> dict:
    if isinstance(content, str):
        content = content.encode("utf-8")
    r = client.post("/api/uploads", files={"file": (name, content, "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()


def run_import(client: TestClient, content: bytes | str, mapping: dict, *, expect: str = "succeeded", **options) -> dict:
    """Upload + confirm + wait. Returns the finished job JSON."""
    up = upload(client, content)
    body = {"mapping": mapping, **options}
    r = client.post(f"/api/uploads/{up['upload_id']}/import", json=body)
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]
    jobs.wait_for(job_id)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == expect, job
    return job


HAND_MAPPING = ColumnMapping(case_id="case_id", activity="activity", timestamp="timestamp", resource="resource",
                             department="department", cost="cost", case_status="status").model_dump()
HAND_OPTIONS = {
    "completion": {"mode": "case_status", "completed_status_values": ["Completed"], "open_status_values": ["Open"]},
    "reference_time": "2024-01-06T00:00:00Z",
    "dataset_name": "hand-check",
}


@pytest.fixture
def hand_dataset(client) -> int:
    job = run_import(client, HAND_CHECK.read_bytes(), HAND_MAPPING, **HAND_OPTIONS)
    return job["dataset_id"]
