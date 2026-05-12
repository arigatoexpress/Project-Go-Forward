"""
Video Generator for Ad Studio

Creates actual MP4 videos from:
- Real property photos (slideshow with transitions)
- Voiceover audio (TTS)
- Subtitles/captions from script

Output: TikTok-ready 9:16 vertical video
"""

import base64
import logging
import os
import tempfile
import time
import uuid

logger = logging.getLogger(__name__)

# Output directory for generated videos
GENERATED_VIDEOS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "generated_videos"
)
os.makedirs(GENERATED_VIDEOS_DIR, exist_ok=True)


def generate_ad_video(
    photos: list[str],
    voiceover_base64: str,
    script_text: str,
    home_name: str,
    platform: str = "tiktok",
    duration_per_photo: float = 3.0,
    transition_type: str = "crossfade",
) -> dict:
    """
    Generate an MP4 video from photos, voiceover, and script.
    """
    try:
        import requests
        from moviepy.editor import (
            AudioFileClip,
            ImageClip,
            concatenate_videoclips,
        )
        from moviepy.video.fx.all import fadein, fadeout
    except ImportError as e:
        return {
            "success": False,
            "error": f"Video generation requires moviepy: {e}. Please ensure FFmpeg is installed.",
        }

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="video_gen_")

        # Decode voiceover audio
        audio_path = os.path.join(temp_dir, "voiceover.mp3")
        with open(audio_path, "wb") as f:
            f.write(base64.b64decode(voiceover_base64))

        # Load audio
        audio = AudioFileClip(audio_path)
        audio_duration = audio.duration

        # Process photos
        image_clips = []
        target_width = 1080
        target_height = 1920 if platform == "tiktok" else 1080

        for i, photo_url in enumerate(photos[:6]):
            try:
                response = requests.get(photo_url, timeout=30)
                response.raise_for_status()

                img_path = os.path.join(temp_dir, f"photo_{i}.jpg")
                with open(img_path, "wb") as f:
                    f.write(response.content)

                clip = ImageClip(img_path)
                clip = resize_clip_to_fill(clip, target_width, target_height)
                clip = clip.set_duration(duration_per_photo)
                clip = fadein(clip, 0.5).fx(fadeout, 0.5)
                image_clips.append(clip)
            except Exception as e:
                logger.warning(f"Failed to process photo {photo_url}: {e}")
                continue

        if not image_clips:
            return {"success": False, "error": "No valid photos could be processed"}

        # Concatenate and loop to match audio
        video = concatenate_videoclips(image_clips, method="compose")
        if video.duration < audio_duration:
            loops_needed = int(audio_duration / video.duration) + 1
            video = concatenate_videoclips([video] * loops_needed, method="compose")
        video = video.subclip(0, audio_duration)

        # Add audio
        video = video.set_audio(audio)

        # Generate filename
        video_id = f"{home_name.replace(' ', '_').lower()}_{uuid.uuid4().hex[:6]}"
        output_filename = f"{video_id}.mp4"
        output_path = os.path.join(GENERATED_VIDEOS_DIR, output_filename)

        # Write video
        video.write_videofile(
            output_path,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=os.path.join(temp_dir, "temp_audio.m4a"),
            remove_temp=True,
            threads=4,
            preset="ultrafast",
        )

        # Cleanup
        video.close()
        audio.close()
        for clip in image_clips:
            clip.close()

        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

        return {
            "success": True,
            "video_id": video_id,
            "filename": output_filename,
            "download_url": f"/api/marketing/videos/{output_filename}",
            "duration_seconds": round(audio_duration, 1),
            "file_size_mb": round(file_size_mb, 2),
            "resolution": f"{target_width}x{target_height}",
            "platform": platform,
            "photos_used": len(image_clips),
        }

    except Exception as e:
        logger.error(f"Video generation failed: {e}")
        return {"success": False, "error": str(e)}
    finally:
        if temp_dir and os.path.exists(temp_dir):
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)


def resize_clip_to_fill(clip, target_width: int, target_height: int):
    """Resize clip to fill target dimensions."""
    clip_ratio = clip.w / clip.h
    target_ratio = target_width / target_height

    if clip_ratio > target_ratio:
        new_height = target_height
        new_width = int(new_height * clip_ratio)
        clip = clip.resize(height=new_height)
        x_center = clip.w / 2
        x1 = int(x_center - target_width / 2)
        clip = clip.crop(x1=x1, y1=0, width=target_width, height=target_height)
    else:
        new_width = target_width
        new_height = int(new_width / clip_ratio)
        clip = clip.resize(width=new_width)
        y_center = clip.h / 2
        y1 = int(y_center - target_height / 2)
        clip = clip.crop(x1=0, y1=y1, width=target_width, height=target_height)

    return clip


def _aspect_ratio_for_platform(platform: str) -> str:
    return "16:9" if platform == "facebook" else "9:16"


def _normalize_veo_duration(duration_seconds: int) -> int:
    """Map UI-friendly durations to Veo-supported short-form durations."""
    requested = int(duration_seconds or 6)
    supported = (4, 6, 8)
    return min(supported, key=lambda item: (abs(item - requested), -item))


def _save_source_image(
    home_photo_url: str | None, image_base64: str | None, temp_dir: str
) -> str | None:
    if image_base64:
        raw = image_base64.split(",", 1)[-1]
        path = os.path.join(temp_dir, "source.png")
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(raw))
        return path
    if home_photo_url:
        import requests

        response = requests.get(home_photo_url, timeout=20)
        response.raise_for_status()
        path = os.path.join(temp_dir, "source.jpg")
        with open(path, "wb") as handle:
            handle.write(response.content)
        return path
    return None


def generate_genai_ad_clip(
    prompt: str,
    home_name: str,
    platform: str = "tiktok",
    home_photo_url: str | None = None,
    image_base64: str | None = None,
    duration_seconds: int = 5,
    include_virtual_presenter: bool = False,
    wait_seconds: int = 120,
) -> dict:
    """Generate a short GenAI ad clip with Google Veo via Vertex AI.

    The legacy video path below creates a slideshow from actual photos. This
    path is for true generative clips. It intentionally disallows real people
    by default; when a presenter is requested, the prompt asks for a clearly
    synthetic THO host rather than a real or lookalike person.
    """
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:
        return {
            "success": False,
            "error": f"Google GenAI SDK unavailable: {exc}",
            "provider": "google-veo",
        }

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    model = os.environ.get("THO_VEO_MODEL", "veo-3.0-fast-generate-001")
    duration_seconds = _normalize_veo_duration(duration_seconds)
    wait_seconds = max(15, min(int(wait_seconds or 120), 300))
    aspect_ratio = _aspect_ratio_for_platform(platform)

    person_generation = (
        types.PersonGeneration.ALLOW_ADULT
        if include_virtual_presenter
        else types.PersonGeneration.DONT_ALLOW
    )
    presenter_guidance = (
        "Use a clearly synthetic, non-real virtual presenter wearing neutral THO brand colors. "
        "Do not imitate any real person or celebrity. "
        if include_virtual_presenter
        else "No people or human presenters. "
    )
    enhanced_prompt = (
        f"Create a polished {duration_seconds}-second {aspect_ratio} social video ad for Texas Home Outlet. "
        f"Featured home: {home_name or 'featured manufactured home'}. "
        f"{presenter_guidance}"
        "Show realistic home-tour motion, clean lighting, Texas retail warmth, and no text overlays or watermarks. "
        f"Creative direction: {prompt}"
    )

    temp_dir = tempfile.mkdtemp(prefix="veo_gen_")
    try:
        image_path = _save_source_image(home_photo_url, image_base64, temp_dir)
        client = genai.Client(vertexai=True, project=project, location=location)
        config = types.GenerateVideosConfig(
            number_of_videos=1,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            person_generation=person_generation,
            enhance_prompt=True,
        )
        kwargs = {
            "model": model,
            "prompt": enhanced_prompt,
            "config": config,
        }
        if image_path:
            kwargs["image"] = types.Image.from_file(location=image_path)

        operation = client.models.generate_videos(**kwargs)
        deadline = time.time() + wait_seconds
        while not getattr(operation, "done", False) and time.time() < deadline:
            time.sleep(8)
            operation = client.operations.get(operation)

        if not getattr(operation, "done", False):
            return {
                "success": False,
                "error": "Veo generation is still processing. Try again shortly or increase wait_seconds.",
                "provider": "google-veo",
                "model_used": model,
                "operation_name": getattr(operation, "name", None),
            }

        operation_error = getattr(operation, "error", None)
        if operation_error:
            message = (
                operation_error.get("message")
                if isinstance(operation_error, dict)
                else str(operation_error)
            )
            return {
                "success": False,
                "error": message or "Veo operation failed.",
                "provider": "google-veo",
                "model_used": model,
                "operation_name": getattr(operation, "name", None),
            }

        videos = getattr(getattr(operation, "response", None), "generated_videos", None) or []
        if not videos:
            return {
                "success": False,
                "error": "Veo returned no video results.",
                "provider": "google-veo",
                "model_used": model,
                "operation_name": getattr(operation, "name", None),
            }

        video = videos[0]
        try:
            client.files.download(file=video.video)
        except Exception:
            pass

        video_id = f"veo_{home_name.replace(' ', '_').lower()[:32]}_{uuid.uuid4().hex[:6]}"
        filename = f"{video_id}.mp4"
        output_path = os.path.join(GENERATED_VIDEOS_DIR, filename)
        video.video.save(output_path)
        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)

        return {
            "success": True,
            "provider": "google-veo",
            "video_id": video_id,
            "filename": filename,
            "download_url": f"/api/marketing/videos/{filename}",
            "duration_seconds": duration_seconds,
            "file_size_mb": round(file_size_mb, 2),
            "resolution": aspect_ratio,
            "platform": platform,
            "model_used": model,
            "operation_name": getattr(operation, "name", None),
            "prompt_used": enhanced_prompt[:300],
        }
    except Exception as exc:
        logger.error("Veo generation failed: %s", exc)
        return {
            "success": False,
            "error": f"Veo generation failed: {exc}",
            "provider": "google-veo",
            "model_used": model,
            "hint": "Confirm Vertex AI, Veo access, billing, and THO_VEO_MODEL in the tho-ai-agent project.",
        }
    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)
