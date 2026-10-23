"""Run Ad Studio's real slideshow path with only production requirements installed."""

import base64
import importlib.util
import io
import sys
import tempfile
import types
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image


def load_video_generator():
    """Load video_generator.py without running tools/__init__.py.

    This file is executed by path from a venv that does not have the repo
    root on sys.path. Preload the SSRF helper under its package name so
    ``from tools.outbound_url import ...`` succeeds. Putting the repo root
    on sys.path would import tools/__init__.py and the document stack.
    """
    tools_dir = Path(__file__).resolve().parents[1] / "tools"
    if "tools.outbound_url" not in sys.modules:
        pkg = sys.modules.get("tools")
        if pkg is None:
            pkg = types.ModuleType("tools")
            pkg.__path__ = [str(tools_dir)]
            pkg.__package__ = "tools"
            sys.modules["tools"] = pkg
        outbound_spec = importlib.util.spec_from_file_location(
            "tools.outbound_url", tools_dir / "outbound_url.py"
        )
        outbound = importlib.util.module_from_spec(outbound_spec)
        sys.modules["tools.outbound_url"] = outbound
        outbound_spec.loader.exec_module(outbound)

    source = tools_dir / "video_generator.py"
    spec = importlib.util.spec_from_file_location("production_video_smoke", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    # Load the real runtime module without initializing unrelated tools/agents.
    module = load_video_generator()

    photo = io.BytesIO()
    Image.new("RGB", (64, 48), "navy").save(photo, format="JPEG")
    voice = io.BytesIO()
    with wave.open(voice, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 1600)
    response = SimpleNamespace(content=photo.getvalue(), raise_for_status=lambda: None)
    with tempfile.TemporaryDirectory() as directory:
        # Mock only external photo I/O; use real audio decode, resize, fade,
        # composition, and FFmpeg audio/video encoding with synthetic fixtures.
        with (
            patch.object(module, "GENERATED_VIDEOS_DIR", directory),
            patch.object(module, "fetch_public_url", return_value=response),
        ):
            result = module.generate_ad_video(
                photos=["https://example.invalid/synthetic.jpg"],
                voiceover_base64=base64.b64encode(voice.getvalue()).decode(),
                script_text="Synthetic smoke",
                home_name="synthetic-smoke",
            )
        assert result["success"], result
        assert result["photos_used"] == 1
        assert result["resolution"] == "1080x1920"
        assert (Path(directory) / result["filename"]).stat().st_size > 0
    print("Production Ad Studio generation (audio, resize, MP4): PASS")


if __name__ == "__main__":
    main()
