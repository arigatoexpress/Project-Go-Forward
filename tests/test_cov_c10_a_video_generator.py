"""Coverage tests for tools/video_generator.py.

All heavy media deps are faked — no real video processing, no network:
- moviepy is replaced by fake modules injected into sys.modules
- google.genai is replaced by a fake module injected into sys.modules
- requests.get is monkeypatched to return canned bytes
- vg.GENERATED_VIDEOS_DIR is redirected to tmp_path (no repo writes)
"""

import base64
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import google as google_pkg

import tools.video_generator as vg

# ---------------------------------------------------------------------------
# Fakes for generate_ad_video (moviepy / requests)
# ---------------------------------------------------------------------------


class FakeAudioFileClip:
    default_duration = 7.0

    def __init__(self, path):
        self.path = path
        self.duration = self.default_duration
        self.closed = False

    def close(self):
        self.closed = True


class FakeImageClip:
    def __init__(self, path):
        self.path = path
        self.w = 1600
        self.h = 900
        self.duration = 0.0
        self.closed = False

    def set_duration(self, duration):
        self.duration = duration
        return self

    def fx(self, *args, **kwargs):
        return self

    def close(self):
        self.closed = True


class FakeVideoClip:
    def __init__(self, duration):
        self.duration = duration
        self.audio = None
        self.closed = False

    def subclip(self, start, end):
        self.subclip_args = (start, end)
        return self

    def set_audio(self, audio):
        self.audio = audio
        return self

    def write_videofile(self, path, **kwargs):
        self.write_kwargs = kwargs
        Path(path).write_bytes(b"fake mp4 payload")

    def close(self):
        self.closed = True


def _fake_concatenate(clips, method=None):
    return FakeVideoClip(duration=sum(getattr(c, "duration", 0.0) for c in clips))


def _install_fake_moviepy(monkeypatch):
    moviepy_mod = types.ModuleType("moviepy")
    editor_mod = types.ModuleType("moviepy.editor")
    editor_mod.AudioFileClip = FakeAudioFileClip
    editor_mod.ImageClip = FakeImageClip
    editor_mod.concatenate_videoclips = _fake_concatenate
    video_mod = types.ModuleType("moviepy.video")
    fx_mod = types.ModuleType("moviepy.video.fx")
    fx_all_mod = types.ModuleType("moviepy.video.fx.all")
    fx_all_mod.fadein = lambda clip, t: clip
    fx_all_mod.fadeout = lambda clip, t: clip
    moviepy_mod.editor = editor_mod
    for name, mod in [
        ("moviepy", moviepy_mod),
        ("moviepy.editor", editor_mod),
        ("moviepy.video", video_mod),
        ("moviepy.video.fx", fx_mod),
        ("moviepy.video.fx.all", fx_all_mod),
    ]:
        monkeypatch.setitem(sys.modules, name, mod)


class FakeResponse:
    def __init__(self, content, error=None):
        self.content = content
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error


def _install_fake_requests(monkeypatch, fail_urls=()):
    import requests as real_requests

    def fake_get(url, timeout=None):
        if url in fail_urls:
            raise RuntimeError(f"download failed: {url}")
        return FakeResponse(content=b"jpeg-bytes")

    monkeypatch.setattr(real_requests, "get", fake_get)


def _redirect_output_dir(monkeypatch, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(vg, "GENERATED_VIDEOS_DIR", str(out))
    return out


# ---------------------------------------------------------------------------
# generate_ad_video
# ---------------------------------------------------------------------------


def test_generate_ad_video_success_loops_slideshow_to_match_audio(monkeypatch, tmp_path):
    """Happy path: 2 good photos + 1 failing photo, audio longer than slideshow
    so the loop-and-subclip branch runs."""
    _install_fake_moviepy(monkeypatch)
    _install_fake_requests(monkeypatch, fail_urls=("https://bad/3.jpg",))
    _redirect_output_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(vg, "resize_clip_to_fill", lambda clip, w, h: clip)

    result = vg.generate_ad_video(
        photos=["https://img/1.jpg", "https://img/2.jpg", "https://bad/3.jpg"],
        voiceover_base64=base64.b64encode(b"fake mp3").decode(),
        script_text="script",
        home_name="Casa Grande",
        platform="tiktok",
        duration_per_photo=3.0,
    )

    assert result["success"] is True
    assert result["photos_used"] == 2
    assert result["resolution"] == "1080x1920"
    assert result["duration_seconds"] == 7.0  # FakeAudioFileClip.default_duration
    assert result["download_url"].endswith(result["filename"])
    assert Path(vg.GENERATED_VIDEOS_DIR, result["filename"]).exists()


def test_generate_ad_video_success_landscape_no_loop(monkeypatch, tmp_path):
    """Audio shorter than the slideshow: the no-loop branch, facebook 1:1 size."""
    _install_fake_moviepy(monkeypatch)
    _install_fake_requests(monkeypatch)
    _redirect_output_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(vg, "resize_clip_to_fill", lambda clip, w, h: clip)
    monkeypatch.setattr(FakeAudioFileClip, "default_duration", 4.0)

    result = vg.generate_ad_video(
        photos=["https://img/1.jpg", "https://img/2.jpg"],
        voiceover_base64=base64.b64encode(b"fake mp3").decode(),
        script_text="script",
        home_name="Casa Grande",
        platform="facebook",
        duration_per_photo=3.0,
    )

    assert result["success"] is True
    assert result["resolution"] == "1080x1080"
    assert result["photos_used"] == 2


def test_generate_ad_video_returns_error_when_moviepy_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "moviepy", None)

    result = vg.generate_ad_video(
        photos=["https://img/1.jpg"],
        voiceover_base64=base64.b64encode(b"fake mp3").decode(),
        script_text="script",
        home_name="Casa Grande",
    )

    assert result["success"] is False
    assert "requires moviepy" in result["error"]


def test_generate_ad_video_returns_error_when_all_photos_fail(monkeypatch, tmp_path):
    _install_fake_moviepy(monkeypatch)
    _install_fake_requests(monkeypatch, fail_urls=("https://bad/1.jpg",))
    _redirect_output_dir(monkeypatch, tmp_path)

    result = vg.generate_ad_video(
        photos=["https://bad/1.jpg"],
        voiceover_base64=base64.b64encode(b"fake mp3").decode(),
        script_text="script",
        home_name="Casa Grande",
    )

    assert result["success"] is False
    assert result["error"] == "No valid photos could be processed"


def test_generate_ad_video_catches_unexpected_pipeline_error(monkeypatch, tmp_path):
    """Invalid base64 voiceover makes base64.b64decode raise inside the pipeline."""
    _install_fake_moviepy(monkeypatch)
    _install_fake_requests(monkeypatch)
    _redirect_output_dir(monkeypatch, tmp_path)

    result = vg.generate_ad_video(
        photos=["https://img/1.jpg"],
        voiceover_base64="a",  # invalid padding -> binascii.Error
        script_text="script",
        home_name="Casa Grande",
    )

    assert result["success"] is False
    assert result["error"]


# ---------------------------------------------------------------------------
# _save_source_image / _aspect_ratio_for_platform
# ---------------------------------------------------------------------------


def test_save_source_image_prefers_base64(tmp_path):
    payload = base64.b64encode(b"png-bytes").decode()
    path = vg._save_source_image(
        "https://x/y.jpg", f"data:image/png;base64,{payload}", str(tmp_path)
    )
    assert path == str(tmp_path / "source.png")
    assert Path(path).read_bytes() == b"png-bytes"


def test_save_source_image_fetches_url(monkeypatch, tmp_path):
    import requests as real_requests

    def fake_get(url, timeout=None):
        assert timeout == 20
        return FakeResponse(content=b"jpg-bytes")

    monkeypatch.setattr(real_requests, "get", fake_get)

    path = vg._save_source_image("https://x/y.jpg", None, str(tmp_path))
    assert path == str(tmp_path / "source.jpg")
    assert Path(path).read_bytes() == b"jpg-bytes"


def test_save_source_image_returns_none_without_inputs(tmp_path):
    assert vg._save_source_image(None, None, str(tmp_path)) is None


def test_aspect_ratio_for_platform():
    assert vg._aspect_ratio_for_platform("facebook") == "16:9"
    assert vg._aspect_ratio_for_platform("tiktok") == "9:16"


# ---------------------------------------------------------------------------
# generate_genai_ad_clip (Google Veo) — fake genai SDK
# ---------------------------------------------------------------------------


class FakePersonGeneration:
    ALLOW_ADULT = "allow_adult"
    DONT_ALLOW = "dont_allow"


class FakeGenerateVideosConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeGenaiImage:
    @classmethod
    def from_file(cls, location):
        obj = cls()
        obj.location = location
        return obj


class FakeVideoBlob:
    def save(self, path):
        Path(path).write_bytes(b"veo mp4 payload")


class FakeModels:
    def __init__(self, bucket):
        self.bucket = bucket

    def generate_videos(self, **kwargs):
        self.bucket["captured_kwargs"] = kwargs
        if self.bucket.get("raise_on_generate"):
            raise RuntimeError("generate boom")
        return self.bucket["initial_operation"]


class FakeOperations:
    def __init__(self, bucket):
        self.bucket = bucket

    def get(self, operation):
        self.bucket["get_calls"] = self.bucket.get("get_calls", 0) + 1
        return self.bucket.get("next_operation", operation)


class FakeFiles:
    def __init__(self, bucket):
        self.bucket = bucket

    def download(self, file):
        if self.bucket.get("download_raises"):
            raise RuntimeError("download boom")


def _make_client_class(bucket):
    class FakeGenaiClient:
        def __init__(self, **kwargs):
            bucket["init_kwargs"] = kwargs
            self.models = FakeModels(bucket)
            self.operations = FakeOperations(bucket)
            self.files = FakeFiles(bucket)

    return FakeGenaiClient


def _install_fake_genai(monkeypatch, client_cls):
    genai_mod = types.ModuleType("google.genai")
    types_mod = types.ModuleType("google.genai.types")
    types_mod.PersonGeneration = FakePersonGeneration
    types_mod.GenerateVideosConfig = FakeGenerateVideosConfig
    types_mod.Image = FakeGenaiImage
    genai_mod.Client = client_cls
    genai_mod.types = types_mod
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)
    # `from google import genai` consults the package attribute first when it
    # exists, so make sure it points at the fake too.
    monkeypatch.setattr(google_pkg, "genai", genai_mod, raising=False)


class FakeTime:
    """Scripted time.time() values plus a recording sleep."""

    def __init__(self, values):
        self._it = iter(values)
        self.slept = []

    def time(self):
        return next(self._it)

    def sleep(self, seconds):
        self.slept.append(seconds)


def _done_operation(name="op-1", error=None, videos=None):
    response = SimpleNamespace(generated_videos=videos) if videos is not None else None
    return SimpleNamespace(done=True, name=name, error=error, response=response)


def test_generate_genai_ad_clip_success_with_polling_and_presenter(monkeypatch, tmp_path):
    """Operation pending -> one poll -> done. Covers presenter branch, facebook
    aspect ratio, base64 source image, and the polling loop."""
    _redirect_output_dir(monkeypatch, tmp_path)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.delenv("THO_VEO_MODEL", raising=False)
    bucket = {}
    bucket["initial_operation"] = SimpleNamespace(
        done=False, name="op-1", error=None, response=None
    )
    blob = FakeVideoBlob()
    bucket["next_operation"] = _done_operation(
        videos=[SimpleNamespace(video=blob)]
    )
    _install_fake_genai(monkeypatch, _make_client_class(bucket))
    fake_time = FakeTime([1000.0, 1000.0, 1000.0])
    monkeypatch.setattr(vg, "time", fake_time)

    payload = base64.b64encode(b"png-bytes").decode()
    result = vg.generate_genai_ad_clip(
        prompt="sweeping drone shot",
        home_name="Hill Country Retreat",
        platform="facebook",
        image_base64=f"data:image/png;base64,{payload}",
        duration_seconds=5,
        include_virtual_presenter=True,
        wait_seconds=1,  # clamped up to 15
    )

    assert result["success"] is True
    assert result["provider"] == "google-veo"
    assert result["resolution"] == "16:9"
    assert result["duration_seconds"] == 6  # normalized from 5
    assert result["operation_name"] == "op-1"
    assert result["filename"].endswith(".mp4")
    assert Path(vg.GENERATED_VIDEOS_DIR, result["filename"]).exists()
    assert bucket["get_calls"] == 1
    assert fake_time.slept == [8]
    assert bucket["init_kwargs"]["project"] == "tho-ai-agent"
    assert bucket["init_kwargs"]["location"] == "us-central1"
    kwargs = bucket["captured_kwargs"]
    assert kwargs["config"].kwargs["person_generation"] == "allow_adult"
    assert kwargs["config"].kwargs["aspect_ratio"] == "16:9"
    assert kwargs["image"].location.endswith("source.png")
    assert "6-second" in kwargs["prompt"]
    assert "virtual presenter" in kwargs["prompt"]


def test_generate_genai_ad_clip_success_tiktok_defaults_with_url_image(monkeypatch, tmp_path):
    """Operation done immediately (no polling), tiktok defaults, URL source image."""
    _redirect_output_dir(monkeypatch, tmp_path)
    _install_fake_requests(monkeypatch)
    bucket = {}
    bucket["initial_operation"] = _done_operation(
        name="op-2", videos=[SimpleNamespace(video=FakeVideoBlob())]
    )
    _install_fake_genai(monkeypatch, _make_client_class(bucket))
    monkeypatch.setattr(vg, "time", FakeTime([1000.0]))

    result = vg.generate_genai_ad_clip(
        prompt="walkthrough",
        home_name="Casa",
        home_photo_url="https://x/y.jpg",
    )

    assert result["success"] is True
    assert result["resolution"] == "9:16"
    assert result["operation_name"] == "op-2"
    assert bucket.get("get_calls", 0) == 0
    kwargs = bucket["captured_kwargs"]
    assert kwargs["config"].kwargs["person_generation"] == "dont_allow"
    assert kwargs["image"].location.endswith("source.jpg")
    assert "No people" in kwargs["prompt"]


def test_generate_genai_ad_clip_times_out_while_processing(monkeypatch, tmp_path):
    _redirect_output_dir(monkeypatch, tmp_path)
    bucket = {}
    bucket["initial_operation"] = SimpleNamespace(
        done=False, name="op-timeout", error=None, response=None
    )
    _install_fake_genai(monkeypatch, _make_client_class(bucket))
    # deadline = 1000 + 300 (wait_seconds=999 clamps down to 300); the third
    # clock read pushes past it so the polling loop exits.
    monkeypatch.setattr(vg, "time", FakeTime([1000.0, 1000.0, 2000.0]))

    result = vg.generate_genai_ad_clip(
        prompt="p", home_name="h", wait_seconds=999
    )

    assert result["success"] is False
    assert "still processing" in result["error"]
    assert result["operation_name"] == "op-timeout"
    assert result["model_used"] == os.environ.get(
        "THO_VEO_MODEL", "veo-3.0-fast-generate-001"
    )


def test_generate_genai_ad_clip_returns_operation_error_message(monkeypatch, tmp_path):
    _redirect_output_dir(monkeypatch, tmp_path)
    bucket = {}
    bucket["initial_operation"] = _done_operation(
        name="op-err", error={"message": "render failed"}
    )
    _install_fake_genai(monkeypatch, _make_client_class(bucket))
    monkeypatch.setattr(vg, "time", FakeTime([1000.0]))

    result = vg.generate_genai_ad_clip(prompt="p", home_name="h")

    assert result["success"] is False
    assert result["error"] == "render failed"


def test_generate_genai_ad_clip_stringifies_non_dict_operation_error(monkeypatch, tmp_path):
    _redirect_output_dir(monkeypatch, tmp_path)
    bucket = {}
    bucket["initial_operation"] = _done_operation(
        name="op-err2", error=ValueError("quota exceeded")
    )
    _install_fake_genai(monkeypatch, _make_client_class(bucket))
    monkeypatch.setattr(vg, "time", FakeTime([1000.0]))

    result = vg.generate_genai_ad_clip(prompt="p", home_name="h")

    assert result["success"] is False
    assert "quota exceeded" in result["error"]


def test_generate_genai_ad_clip_reports_missing_videos(monkeypatch, tmp_path):
    _redirect_output_dir(monkeypatch, tmp_path)
    bucket = {}
    bucket["initial_operation"] = _done_operation(name="op-empty", videos=[])
    _install_fake_genai(monkeypatch, _make_client_class(bucket))
    monkeypatch.setattr(vg, "time", FakeTime([1000.0]))

    result = vg.generate_genai_ad_clip(prompt="p", home_name="h")

    assert result["success"] is False
    assert "no video results" in result["error"]


def test_generate_genai_ad_clip_download_failure_is_ignored(monkeypatch, tmp_path):
    """files.download raising must not fail the otherwise-successful job."""
    _redirect_output_dir(monkeypatch, tmp_path)
    bucket = {"download_raises": True}
    bucket["initial_operation"] = _done_operation(
        videos=[SimpleNamespace(video=FakeVideoBlob())]
    )
    _install_fake_genai(monkeypatch, _make_client_class(bucket))
    monkeypatch.setattr(vg, "time", FakeTime([1000.0]))

    result = vg.generate_genai_ad_clip(prompt="p", home_name="h")

    assert result["success"] is True


def test_generate_genai_ad_clip_wraps_client_failures(monkeypatch, tmp_path):
    _redirect_output_dir(monkeypatch, tmp_path)
    bucket = {"raise_on_generate": True}

    class BrokenClient:
        def __init__(self, **kwargs):
            self.models = FakeModels(bucket)
            self.operations = FakeOperations(bucket)
            self.files = FakeFiles(bucket)

    _install_fake_genai(monkeypatch, BrokenClient)
    monkeypatch.setattr(vg, "time", FakeTime([1000.0]))

    result = vg.generate_genai_ad_clip(prompt="p", home_name="h")

    assert result["success"] is False
    assert "Veo generation failed" in result["error"]
    assert "hint" in result


def test_generate_genai_ad_clip_returns_error_when_sdk_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.genai", None)

    result = vg.generate_genai_ad_clip(prompt="p", home_name="h")

    assert result["success"] is False
    assert "Google GenAI SDK unavailable" in result["error"]
    assert result["provider"] == "google-veo"
