"""Tests for the Ad Studio generation/download endpoints (coverage gaps).

Targets flagged by the Python-3.12 honest coverage re-measurement (see
docs/MAIN_COVERAGE_INVESTIGATION.md):

- ``POST /api/marketing/generate-voiceover`` — TTS voiceover generation
- ``GET  /api/marketing/voiceover-voices``  — TTS voice catalog
- ``POST /api/marketing/generate-image``    — Imagen image generation
- ``POST /api/marketing/generate-video``    — MP4 composer
- ``POST /api/marketing/generate-genai-clip`` — Veo/Vertex GenAI clip
- ``GET  /api/marketing/videos/{filename}`` — generated-video download

``tools.marketing_tools`` and ``tools.video_generator`` are already stubbed
by ``load_app`` (tests/test_api_v1.py); per-test fakes are swapped onto those
stub module attributes. Traversal variants of the download endpoint are
invoked directly because TestClient normalizes dotted paths client-side; the
validation branches return before touching the filesystem, so no request
object is needed. ``GENERATED_VIDEOS_DIR`` is pointed at pytest's ``tmp_path``
so no repo files are touched.

Run: python -m pytest tests/test_ad_studio_generate_endpoints.py -v
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).parent))
from test_api_v1 import create_client  # noqa: E402


def _admin_headers(main) -> dict:
    return {"X-Admin-Token": main._create_admin_token()}


def _marketing_stub(monkeypatch) -> types.ModuleType:
    """The load_app-provided tools.marketing_tools stub for this test.

    The endpoint imports from the submodule, which resolves through
    sys.modules — that stub is authoritative. The package attribute is
    patched to match so nothing can observe the real module instead.
    """
    import tools

    stub = sys.modules["tools.marketing_tools"]
    monkeypatch.setattr(tools, "marketing_tools", stub, raising=False)
    return stub


# ─── POST /api/marketing/generate-voiceover ───────────────────────────────


class TestGenerateVoiceover:
    def test_requires_script_text(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        # The endpoint imports this name before validating the body.
        monkeypatch.setattr(
            _marketing_stub(monkeypatch),
            "generate_script_voiceover",
            lambda **kwargs: {"success": True},
            raising=False,
        )

        resp = client.post(
            "/api/marketing/generate-voiceover",
            json={"voice": "en-US-Neural2-D"},
            headers=_admin_headers(main),
        )

        assert resp.json() == {"success": False, "error": "script_text is required"}

    def test_success_passes_args_and_result(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        calls: list = []
        stub = _marketing_stub(monkeypatch)
        monkeypatch.setattr(
            stub,
            "generate_script_voiceover",
            lambda **kwargs: calls.append(kwargs) or {"success": True, "audio_base64": "QUJD"},
            raising=False,
        )

        resp = client.post(
            "/api/marketing/generate-voiceover",
            json={"script_text": "Hi there", "voice": "en-US-Studio-Q", "speaking_rate": 1.2},
            headers=_admin_headers(main),
        )

        assert resp.json() == {"success": True, "audio_base64": "QUJD"}
        assert calls == [{"script_text": "Hi there", "voice": "en-US-Studio-Q", "speaking_rate": 1.2}]

    def test_generation_failure_returns_generic_error(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        stub = _marketing_stub(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("tts down")

        monkeypatch.setattr(stub, "generate_script_voiceover", _boom, raising=False)

        resp = client.post(
            "/api/marketing/generate-voiceover",
            json={"script_text": "Hi"},
            headers=_admin_headers(main),
        )

        assert resp.json() == {
            "success": False,
            "error": "Voiceover generation failed. Please try again.",
        }


# ─── GET /api/marketing/voiceover-voices ──────────────────────────────────


class TestVoiceoverVoices:
    def test_lists_configured_voices(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        stub = _marketing_stub(monkeypatch)
        monkeypatch.setattr(
            stub, "TTS_VOICES", ["en-US-Neural2-D", "en-US-Studio-Q"], raising=False
        )

        resp = client.get("/api/marketing/voiceover-voices", headers=_admin_headers(main))

        assert resp.json() == {
            "success": True,
            "voices": ["en-US-Neural2-D", "en-US-Studio-Q"],
        }

    def test_catalog_failure_returns_error(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        stub = _marketing_stub(monkeypatch)
        monkeypatch.delattr(stub, "TTS_VOICES", raising=False)

        resp = client.get("/api/marketing/voiceover-voices", headers=_admin_headers(main))

        assert resp.json() == {"success": False, "error": "Failed to load voices"}


# ─── POST /api/marketing/generate-image ───────────────────────────────────


class TestGenerateImage:
    def test_requires_prompt(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)

        resp = client.post(
            "/api/marketing/generate-image",
            json={"prompt": "   "},
            headers=_admin_headers(main),
        )

        assert resp.json() == {
            "success": False,
            "error": "A prompt is required to generate an image.",
        }

    def test_success_passes_args_and_result(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        calls: list = []
        monkeypatch.setattr(
            main,
            "generate_ad_image",
            lambda **kwargs: calls.append(kwargs) or {"success": True, "image_url": "https://cdn/x.png"},
        )

        resp = client.post(
            "/api/marketing/generate-image",
            json={
                "prompt": "sunset home",
                "home_name": "Creole 3256",
                "platform": "instagram_reels",
                "style": "cinematic",
                "aspect_ratio": "9:16",
            },
            headers=_admin_headers(main),
        )

        assert resp.json() == {"success": True, "image_url": "https://cdn/x.png"}
        assert calls == [
            {
                "prompt": "sunset home",
                "home_name": "Creole 3256",
                "platform": "instagram_reels",
                "style": "cinematic",
                "aspect_ratio": "9:16",
            }
        ]

    def test_generation_failure_returns_generic_error(self, monkeypatch):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)

        def _boom(**kwargs):
            raise RuntimeError("imagen quota")

        monkeypatch.setattr(main, "generate_ad_image", _boom)

        resp = client.post(
            "/api/marketing/generate-image",
            json={"prompt": "sunset"},
            headers=_admin_headers(main),
        )

        assert resp.json() == {
            "success": False,
            "error": "Image generation failed. Please try again.",
        }


# ─── POST /api/marketing/generate-video ────────────────────────────────────


def _video_stub(monkeypatch) -> types.ModuleType:
    """The load_app-provided tools.video_generator stub for this test.

    The real ``tools`` package never imports this submodule in its init
    block, so there is no package attribute — reach it through sys.modules.
    The package attribute is patched too so a full-suite run that imported
    the real submodule earlier cannot shadow the per-test stub.
    """
    import tools

    stub = sys.modules["tools.video_generator"]
    monkeypatch.setattr(tools, "video_generator", stub, raising=False)
    return stub


class TestGenerateVideo:
    def _client(self, monkeypatch):
        client, main, db, logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        return client, main, db, logger

    def test_requires_at_least_one_photo(self, monkeypatch):
        client, main, _db, _l = self._client(monkeypatch)

        resp = client.post(
            "/api/marketing/generate-video",
            json={"voiceover_base64": "QUJD"},
            headers=_admin_headers(main),
        )

        assert resp.json() == {"success": False, "error": "At least one photo is required"}

    def test_requires_voiceover(self, monkeypatch):
        client, main, _db, _l = self._client(monkeypatch)

        resp = client.post(
            "/api/marketing/generate-video",
            json={"photos": ["p1.jpg"]},
            headers=_admin_headers(main),
        )

        assert resp.json() == {"success": False, "error": "Voiceover audio is required"}

    def test_success_passes_args_and_result(self, monkeypatch):
        client, main, _db, _l = self._client(monkeypatch)
        calls: list = []
        monkeypatch.setattr(
            _video_stub(monkeypatch),
            "generate_ad_video",
            lambda **kwargs: calls.append(kwargs)
            or {"success": True, "video_url": "/api/marketing/videos/ad.mp4"},
        )

        resp = client.post(
            "/api/marketing/generate-video",
            json={
                "photos": ["p1.jpg", "p2.jpg"],
                "voiceover_base64": "QUJD",
                "script_text": "Tour today",
                "home_name": "Creole",
                "platform": "tiktok",
                "duration_per_photo": 2.5,
            },
            headers=_admin_headers(main),
        )

        assert resp.json() == {"success": True, "video_url": "/api/marketing/videos/ad.mp4"}
        assert calls == [
            {
                "photos": ["p1.jpg", "p2.jpg"],
                "voiceover_base64": "QUJD",
                "script_text": "Tour today",
                "home_name": "Creole",
                "platform": "tiktok",
                "duration_per_photo": 2.5,
            }
        ]

    def test_generation_failure_echoes_error(self, monkeypatch):
        client, main, _db, _l = self._client(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("ffmpeg missing")

        monkeypatch.setattr(_video_stub(monkeypatch), "generate_ad_video", _boom)

        resp = client.post(
            "/api/marketing/generate-video",
            json={"photos": ["p1.jpg"], "voiceover_base64": "QUJD"},
            headers=_admin_headers(main),
        )

        assert resp.json() == {
            "success": False,
            "error": "Video generation failed: ffmpeg missing",
        }


# ─── POST /api/marketing/generate-genai-clip ──────────────────────────────


class TestGenerateGenaiClip:
    def _client(self, monkeypatch):
        client, main, db, logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "log_admin_action", lambda **kwargs: None)
        monkeypatch.setattr(main, "log_user_action", lambda **kwargs: None)
        # The endpoint imports this name before validating the prompt, so it
        # must exist on the stub even for the rejection-path tests (the
        # load_app stub only ships generate_ad_video).
        monkeypatch.setattr(
            _video_stub(monkeypatch),
            "generate_genai_ad_clip",
            lambda **kwargs: {"success": True},
            raising=False,
        )
        return client, main, db, logger

    def test_requires_prompt(self, monkeypatch):
        client, main, _db, _l = self._client(monkeypatch)

        resp = client.post(
            "/api/marketing/generate-genai-clip",
            json={"home_name": "Creole"},
            headers=_admin_headers(main),
        )

        assert resp.json() == {
            "success": False,
            "error": "A prompt is required for GenAI clip generation",
        }

    def test_success_casts_presenter_flag_and_passes_args(self, monkeypatch):
        client, main, _db, _l = self._client(monkeypatch)
        calls: list = []
        monkeypatch.setattr(
            _video_stub(monkeypatch),
            "generate_genai_ad_clip",
            lambda **kwargs: calls.append(kwargs)
            or {"success": True, "video_url": "/api/marketing/videos/clip.mp4"},
        )

        resp = client.post(
            "/api/marketing/generate-genai-clip",
            json={
                "prompt": "Drone flyover",
                "home_name": "Creole",
                "home_photo_url": "https://cdn/p.jpg",
                "duration_seconds": 8,
                "include_virtual_presenter": "yes",
                "wait_seconds": 60,
            },
            headers=_admin_headers(main),
        )

        assert resp.json() == {"success": True, "video_url": "/api/marketing/videos/clip.mp4"}
        assert calls == [
            {
                "prompt": "Drone flyover",
                "home_name": "Creole",
                "platform": "tiktok",
                "home_photo_url": "https://cdn/p.jpg",
                "image_base64": None,
                "duration_seconds": 8,
                "include_virtual_presenter": True,
                "wait_seconds": 60,
            }
        ]

    def test_generation_failure_echoes_error(self, monkeypatch):
        client, main, _db, _l = self._client(monkeypatch)

        def _boom(**kwargs):
            raise RuntimeError("veo quota")

        monkeypatch.setattr(_video_stub(monkeypatch), "generate_genai_ad_clip", _boom)

        resp = client.post(
            "/api/marketing/generate-genai-clip",
            json={"prompt": "Drone flyover"},
            headers=_admin_headers(main),
        )

        assert resp.json() == {
            "success": False,
            "error": "GenAI clip generation failed: veo quota",
        }


# ─── GET /api/marketing/videos/{filename} ──────────────────────────────────


class TestDownloadGeneratedVideo:
    def test_invalid_extension_rejected(self, monkeypatch):
        client, _main, _db, _logger = create_client(monkeypatch)

        resp = client.get(
            "/api/marketing/videos/notes.txt",
            headers={"X-Admin-Token": _main._create_admin_token()},
        )

        assert resp.json() == {"error": "Only MP4 files allowed"}

    def test_missing_file_returns_not_found(self, monkeypatch, tmp_path):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "GENERATED_VIDEOS_DIR", str(tmp_path))

        resp = client.get(
            "/api/marketing/videos/ghost.mp4",
            headers=_admin_headers(main),
        )

        assert resp.json() == {"error": "File not found"}

    def test_existing_mp4_downloaded(self, monkeypatch, tmp_path):
        client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "GENERATED_VIDEOS_DIR", str(tmp_path))
        (tmp_path / "ad.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp4")

        resp = client.get(
            "/api/marketing/videos/ad.mp4",
            headers=_admin_headers(main),
        )

        assert resp.status_code == 200
        assert resp.content == b"\x00\x00\x00\x18ftypmp4"
        assert resp.headers["content-type"] == "video/mp4"
        assert 'filename="ad.mp4"' in resp.headers["content-disposition"]

    def test_traversal_variants_rejected_directly(self, monkeypatch, tmp_path):
        _client, main, _db, _logger = create_client(monkeypatch)
        monkeypatch.setattr(main, "GENERATED_VIDEOS_DIR", str(tmp_path))

        # TestClient normalizes dotted paths client-side; call directly.
        for bad_name in ("../secret.mp4", "sub/ad.mp4", ".."):
            result = asyncio.run(main.download_generated_video(bad_name))
            assert result == {"error": "Invalid filename"}, bad_name
