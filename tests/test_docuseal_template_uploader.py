"""
Tests for tools/docuseal_template_uploader.py

Covers:
  1. DRY-RUN makes no HTTP calls
  2. --apply with a mocked DocuSeal API returns success and writes the mapping
  3. Skip-if-already-mapped logic (without --force)
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import importlib.util

import pytest

# Import the uploader directly from its file path to avoid triggering tools/__init__.py,
# which transitively imports pypdf and other heavy deps not available in CI.
# Registering it in sys.modules lets unittest.mock.patch resolve it without going
# through the package namespace.
_MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "docuseal_template_uploader.py"
_spec = importlib.util.spec_from_file_location("docuseal_template_uploader", _MODULE_PATH)
uploader = importlib.util.module_from_spec(_spec)
sys.modules["docuseal_template_uploader"] = uploader
_spec.loader.exec_module(uploader)

# Patch target prefix — matches the sys.modules key above
_MOD = "docuseal_template_uploader"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_repo(tmp_path, monkeypatch):
    """Create a minimal repo layout in a temp directory."""
    tho_docs = tmp_path / "tho_documents"
    tho_docs.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    # Create 3 fake PDF files
    for name in ("Alpha.pdf", "Bravo.pdf", "Charlie.pdf"):
        (tho_docs / name).write_bytes(b"%PDF-1.4 fake")

    # Patch module-level path constants
    monkeypatch.setattr(uploader, "TEMPLATES_DIR", tho_docs)
    monkeypatch.setattr(uploader, "MAPPING_FILE", config_dir / "docuseal_templates.json")
    monkeypatch.setattr(uploader, "REPORT_DIR", tools_dir)

    return tmp_path


# ---------------------------------------------------------------------------
# Test 1: DRY-RUN makes no HTTP calls
# ---------------------------------------------------------------------------

def test_dry_run_makes_no_http_calls(tmp_repo):
    with patch(f"{_MOD}.requests.post") as mock_post:
        uploader.run(api_url="", api_token="", apply=False, force=False)

    mock_post.assert_not_called()


def test_dry_run_all_results_are_dryrun_status(tmp_repo, capsys):
    uploader.run(api_url="", api_token="", apply=False, force=False)
    captured = capsys.readouterr()
    assert "DRY" in captured.out
    # No UPLOADED or ERROR lines
    assert "UPLOADED" not in captured.out
    assert "ERROR" not in captured.out


def test_dry_run_does_not_write_mapping(tmp_repo):
    mapping_path = uploader.MAPPING_FILE
    assert not mapping_path.exists(), "mapping should not exist before run"
    uploader.run(api_url="", api_token="", apply=False, force=False)
    assert not mapping_path.exists(), "mapping should not be created in dry-run mode"


# ---------------------------------------------------------------------------
# Test 2: --apply with mocked DocuSeal writes mapping
# ---------------------------------------------------------------------------

def _make_docuseal_response(template_id: int, fields_count: int = 5):
    """Build a minimal DocuSeal-style response dict."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "id": template_id,
        "name": f"Template {template_id}",
        "fields": [{"name": f"field_{i}"} for i in range(fields_count)],
    }
    return mock_resp


def test_apply_uploads_all_pdfs_and_writes_mapping(tmp_repo):
    responses = [
        _make_docuseal_response(101, 3),
        _make_docuseal_response(102, 7),
        _make_docuseal_response(103, 0),
    ]

    with patch(f"{_MOD}.requests.post", side_effect=responses) as mock_post, \
         patch(f"{_MOD}.time.sleep"):
        uploader.run(
            api_url="https://sign.example.com",
            api_token="test-token",
            apply=True,
            force=False,
        )

    assert mock_post.call_count == 3

    mapping = json.loads(uploader.MAPPING_FILE.read_text())
    assert set(mapping.keys()) == {"Alpha.pdf", "Bravo.pdf", "Charlie.pdf"}
    assert mapping["Alpha.pdf"]["docuseal_template_id"] == 101
    assert mapping["Alpha.pdf"]["fields_count"] == 3
    assert mapping["Bravo.pdf"]["docuseal_template_id"] == 102
    assert mapping["Charlie.pdf"]["docuseal_template_id"] == 103


def test_apply_uses_correct_url_and_token(tmp_repo):
    with patch(f"{_MOD}.requests.post",
               side_effect=[_make_docuseal_response(1)] * 3) as mock_post, \
         patch(f"{_MOD}.time.sleep"):
        uploader.run(
            api_url="https://sign.example.com",
            api_token="secret-abc",
            apply=True,
            force=False,
        )

    call = mock_post.call_args_list[0]
    assert call[1]["headers"]["X-Auth-Token"] == "secret-abc"
    assert call[0][0] == "https://sign.example.com/api/templates/pdf"


def test_apply_writes_report_file(tmp_repo):
    with patch(f"{_MOD}.requests.post",
               side_effect=[_make_docuseal_response(i) for i in range(3)]), \
         patch(f"{_MOD}.time.sleep"):
        uploader.run(
            api_url="https://sign.example.com",
            api_token="tok",
            apply=True,
            force=False,
        )

    reports = list((tmp_repo / "tools").glob(".docuseal-upload-report-*.md"))
    assert len(reports) == 1
    content = reports[0].read_text()
    assert "UPLOADED" in content


# ---------------------------------------------------------------------------
# Test 3: Skip-if-already-mapped
# ---------------------------------------------------------------------------

def test_skip_already_mapped_without_force(tmp_repo):
    # Pre-populate mapping with Alpha.pdf
    existing = {
        "Alpha.pdf": {"docuseal_template_id": 999, "fields_count": 4}
    }
    uploader.MAPPING_FILE.write_text(json.dumps(existing))

    responses = [
        _make_docuseal_response(102, 2),
        _make_docuseal_response(103, 6),
    ]

    with patch(f"{_MOD}.requests.post", side_effect=responses) as mock_post, \
         patch(f"{_MOD}.time.sleep"):
        uploader.run(
            api_url="https://sign.example.com",
            api_token="tok",
            apply=True,
            force=False,
        )

    # Only 2 calls (Bravo + Charlie), Alpha was skipped
    assert mock_post.call_count == 2

    mapping = json.loads(uploader.MAPPING_FILE.read_text())
    # Alpha.pdf should be unchanged
    assert mapping["Alpha.pdf"]["docuseal_template_id"] == 999


def test_force_re_uploads_already_mapped(tmp_repo):
    existing = {
        "Alpha.pdf": {"docuseal_template_id": 999, "fields_count": 4},
        "Bravo.pdf": {"docuseal_template_id": 998, "fields_count": 2},
        "Charlie.pdf": {"docuseal_template_id": 997, "fields_count": 1},
    }
    uploader.MAPPING_FILE.write_text(json.dumps(existing))

    responses = [
        _make_docuseal_response(201, 5),
        _make_docuseal_response(202, 5),
        _make_docuseal_response(203, 5),
    ]

    with patch(f"{_MOD}.requests.post", side_effect=responses) as mock_post, \
         patch(f"{_MOD}.time.sleep"):
        uploader.run(
            api_url="https://sign.example.com",
            api_token="tok",
            apply=True,
            force=True,
        )

    assert mock_post.call_count == 3
    mapping = json.loads(uploader.MAPPING_FILE.read_text())
    # IDs should be updated
    assert mapping["Alpha.pdf"]["docuseal_template_id"] == 201
    assert mapping["Bravo.pdf"]["docuseal_template_id"] == 202


def test_skip_does_not_overwrite_existing_id(tmp_repo):
    """Verify that skipped entries keep their original IDs intact in the mapping."""
    existing = {"Alpha.pdf": {"docuseal_template_id": 42, "fields_count": 10}}
    uploader.MAPPING_FILE.write_text(json.dumps(existing))

    # Bravo and Charlie get uploaded; Alpha is skipped
    responses = [_make_docuseal_response(201), _make_docuseal_response(202)]
    with patch(f"{_MOD}.requests.post", side_effect=responses), \
         patch(f"{_MOD}.time.sleep"):
        uploader.run(
            api_url="https://sign.example.com",
            api_token="tok",
            apply=True,
            force=False,
        )

    mapping = json.loads(uploader.MAPPING_FILE.read_text())
    assert mapping["Alpha.pdf"]["docuseal_template_id"] == 42


# ---------------------------------------------------------------------------
# Test 4: CLI argument validation
# ---------------------------------------------------------------------------

def test_main_exits_when_apply_missing_url(tmp_repo, capsys):
    with pytest.raises(SystemExit) as exc_info:
        uploader.main(["--apply", "--token", "tok"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "DOCUSEAL_API_URL" in captured.err


def test_main_exits_when_apply_missing_token(tmp_repo, capsys):
    with pytest.raises(SystemExit) as exc_info:
        uploader.main(["--apply", "--url", "https://sign.example.com"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "DOCUSEAL_API_TOKEN" in captured.err


def test_main_dry_run_does_not_require_url_or_token(tmp_repo):
    # Should not raise even with no url/token (dry-run is the default)
    uploader.main([])
