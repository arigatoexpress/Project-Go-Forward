"""
Marketing Tools for Texas Home Outlet AI Marketing Agent ("Tex").

Enhanced with Google GenAI capabilities:
- Imagen image generation for ad visuals
- Gemini 2.5 Flash for smarter script generation
- Real inventory integration with actual property photos & Matterport tours
- Two-pass quality scoring to eliminate AI slop
- A/B script variations
- Platform-specific optimization
- Brand style guide enforcement
"""

import base64
import json
import logging
import os
import uuid
from datetime import datetime

try:
    from google.adk.tools import ToolContext
except ImportError:
    ToolContext = None

logger = logging.getLogger(__name__)


def _split_env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def _user_safe_ai_error(exc: Exception | str) -> dict:
    """Map a raw GenAI/Vertex exception to a client-safe error payload.

    Raw Vertex AI / Gemini exceptions can embed infrastructure details — GCP
    project numbers, billing/dunning internals, stack traces — that must never
    reach end users. Call sites log the full exception; this returns only a
    friendly, actionable message plus an ``error_code`` so ops/the frontend can
    correlate without exposing internals.
    """
    lowered = str(exc).lower()

    # Billing / account suspension (e.g. GCP "dunning decision is deny",
    # closed billing account, or a bare PERMISSION_DENIED on the project).
    if (
        "dunning" in lowered
        or "billing" in lowered
        or "consumer_suspended" in lowered
        or ("permission_denied" in lowered and "project" in lowered)
    ):
        return {
            "error": (
                "AI generation is temporarily unavailable while we resolve a "
                "service configuration issue on our end. Our team has been "
                "notified — please try again a little later."
            ),
            "error_code": "ai_service_unavailable",
        }

    # Quota / rate limit.
    if (
        "resource_exhausted" in lowered
        or "429" in lowered
        or "quota" in lowered
        or "rate limit" in lowered
    ):
        return {
            "error": "AI generation is busy right now. Please wait a moment and try again.",
            "error_code": "ai_rate_limited",
        }

    # Auth / credentials.
    if "unauthenticated" in lowered or "credential" in lowered or "401" in lowered:
        return {
            "error": (
                "AI generation is temporarily unavailable due to a service "
                "configuration issue. Our team has been notified."
            ),
            "error_code": "ai_auth_error",
        }

    # Network / transient unavailability.
    if (
        "timeout" in lowered
        or "timed out" in lowered
        or "connection" in lowered
        or "unavailable" in lowered
        or "503" in lowered
    ):
        return {
            "error": "Couldn't reach the AI service. Check your connection and try again.",
            "error_code": "ai_network_error",
        }

    # Generic fallback — never echo the raw exception text.
    return {
        "error": "AI generation failed unexpectedly. Please try again in a moment.",
        "error_code": "ai_unknown_error",
    }


def _gcp_project() -> str:
    return os.environ.get("GOOGLE_CLOUD_PROJECT", "tho-ai-agent")


def _gcp_location() -> str:
    return os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")


def _genai_client():
    from google import genai

    return genai.Client(vertexai=True, project=_gcp_project(), location=_gcp_location())


def get_gcp_ai_readiness() -> dict:
    """Return GCP/GenAI setup status without making paid generation calls."""
    status = {
        "success": True,
        "project": _gcp_project(),
        "location": _gcp_location(),
        "models": {
            "gemini": os.environ.get("THO_GEMINI_MODEL", "gemini-2.5-flash"),
            "imagen": os.environ.get("THO_IMAGEN_MODEL", "imagen-4.0-generate-001"),
            "veo": os.environ.get("THO_VEO_MODEL", "veo-3.0-fast-generate-001"),
        },
        "google_genai_sdk": False,
        "text_to_speech_sdk": False,
        "ready": False,
        "requirements": [],
    }
    try:
        from google import genai as _genai  # noqa: F401

        status["google_genai_sdk"] = True
    except Exception as exc:
        status["requirements"].append(f"Install/repair google-genai: {exc}")
    try:
        from google.cloud import texttospeech as _tts  # noqa: F401

        status["text_to_speech_sdk"] = True
    except Exception as exc:
        status["requirements"].append(f"Install/repair google-cloud-texttospeech: {exc}")
    if not status["project"]:
        status["requirements"].append("Set GOOGLE_CLOUD_PROJECT")
    if not status["location"]:
        status["requirements"].append("Set GOOGLE_CLOUD_LOCATION")
    status["ready"] = (
        status["google_genai_sdk"]
        and status["text_to_speech_sdk"]
        and bool(status["project"])
        and bool(status["location"])
    )
    return status


# ─── Brand Style Guide ───
# These constants enforce THO's brand voice and prevent AI slop

BANNED_WORDS = [
    # Corporate/AI buzzwords
    "nestled",
    "journey",
    "elevate",
    "reimagine",
    "unlock",
    "embark",
    "curated",
    "bespoke",
    "artisanal",
    "synergy",
    "seamless",
    "leverage",
    "revolutionize",
    "transformative",
    "paradigm",
    "holistic",
    "robust",
    "cutting-edge",
    "state-of-the-art",
    "game-changer",
    "next-level",
    "world-class",
    "unparalleled",
    "breathtaking",
    "turn-key",
    # Real estate clichés
    "step into",
    "discover the magic",
    "dream home awaits",
    "luxurious living",
    "hidden gem",
    "oasis",
    "haven",
    "retreat",
    "sanctuary",
    "paradise",
    "charming",
    "quaint",
    "stunning",
    "exquisite",
    "immaculate",
    "boasts",
    "features galore",
    "entertainer's delight",
    "move-in ready",
    # Vague hype with no substance
    "incredible",
    "unbelievable",
    "insane",
    "mind-blowing",
    "life-changing",
    "gorgeous",
    "spectacular",
    "magnificent",
    "extraordinary",
    "phenomenal",
    # AI-sounding filler
    "in today's market",
    "look no further",
    "whether you're a",
    "imagine coming home to",
    "picture yourself",
    "don't miss out on this",
    "what if I told you",
    "the perfect blend of",
    "where luxury meets",
    "redefine what it means",
    "not your grandfather's",
]

THO_BRAND_VOICE = (
    """
TEXAS HOME OUTLET BRAND VOICE — MANDATORY:

TONE: Casual, warm, specific, Texas-friendly. Talk like a real person on camera, not a marketing department.

DO:
- Use exact numbers: "$89,900", "1,680 sqft", "3 bed/2 bath"
- Reference specific rooms: "granite countertops in the kitchen", "walk-in closet in the master"
- Write like you're literally walking through the home, pointing at things
- Use short, punchy sentences. One thought per line.
- Be enthusiastic because the VALUE is real, not because you're selling
- Ground every claim in a specific detail (don't say "spacious" — say "this living room is 18 feet across")
- Include at least one moment of genuine surprise or contrast ("and THIS is only $74k")
- Reference local context: Houston, FM 1960, Texas heat, the lot

DON'T:
- Use corporate buzzwords or AI-sounding phrases
- Use adjectives without proof (never "stunning kitchen" — instead "kitchen with quartz countertops and a 6-foot island")
- Write like a press release, listing description, or brochure
- Use any of these banned words/phrases: """
    + ", ".join(BANNED_WORDS[:20])
    + """
- Start scripts with "Are you looking for..." or "Have you ever dreamed..." or "What if I told you..."
- Use more than 2 exclamation marks in the entire script
- Use more than 1 emoji in the entire script
- Write long flowing paragraphs — this is VIDEO, write in short visual beats

EXAMPLES OF GOOD HOOKS:
- "This 3 bed/2 bath just hit the lot at $89,900. Yeah, you read that right."
- "Your apartment rent could literally buy you THIS house. Let me show you."
- "I'm standing inside a $65,000 home and people keep thinking it's $200K."
- "POV: You stopped paying rent and bought a whole house instead."
- "Everyone told me manufactured homes were ugly. Then I walked into this one."
- "$1,200/month rent. Or $650/month mortgage on THIS. You pick."
- "They quoted me $350K for a stick-built. I got the same thing for $89K."
- "3 bedrooms, 2 baths, and a kitchen bigger than my first apartment. Under $75K."

EXAMPLES OF BAD HOOKS (never write anything like these):
- "Are you looking for your dream home?" (generic, boring, no specifics)
- "Discover the magic of affordable living!" (AI slop, zero substance)
- "What if I told you homeownership is within reach?" (cliché clickbait)
- "Step into luxury at an unbelievable price!" (every banned word at once)
- "In today's competitive housing market..." (puts people to sleep)
"""
)


# ─── Inventory Integration ───


def _load_inventory_for_marketing():
    """Load inventory data for marketing context. Reuses inventory_tools loader."""
    try:
        from tools.inventory_tools import _load_inventory
    except ImportError:
        try:
            from .inventory_tools import _load_inventory
        except ImportError:
            return []

    try:
        inventory = _load_inventory()
        return inventory or []
    except Exception as e:
        logger.warning(f"Failed to load inventory for marketing: {e}")
        return []


def get_inventory_for_ads(limit: int = 5) -> dict:
    """
    Get current inventory highlights for ad creation context.

    Returns top homes with names, prices, specs, and images
    so the ad creator can reference real products.

    Args:
        limit: Max number of homes to return (default 5)

    Returns:
        Dictionary with homes list and summary stats
    """
    inventory = _load_inventory_for_marketing()

    if not inventory:
        return {
            "success": False,
            "homes": [],
            "message": "No inventory data available. You can still create ads with manual home details.",
        }

    # Sort: featured/priced homes first, then by price descending
    priced = [h for h in inventory if h.get("pricing", {}).get("price_value", 0) > 0]
    unpriced = [h for h in inventory if h.get("pricing", {}).get("price_value", 0) == 0]
    priced.sort(key=lambda x: x["pricing"]["price_value"], reverse=True)

    sorted_inv = priced + unpriced
    top_homes = sorted_inv[:limit]

    # Load real property assets (photos + Matterport tours from website)
    try:
        from tools.asset_scraper import get_assets_for_home, get_matterport_url
        from tools.photo_classifier import apply_classifier_to_home, has_real_photo
    except ImportError:
        from .asset_scraper import get_assets_for_home, get_matterport_url
        from .photo_classifier import apply_classifier_to_home, has_real_photo

    homes_for_ads = []
    for h in top_homes:
        gallery_images = h.get("gallery_images", []) or []
        real_photos = h.get("real_photos", []) or gallery_images
        floor_plan_url = h.get("floor_plan_url") or h.get("floorplan_url")
        matterport_id = h.get("matterport_id")
        matterport_url = h.get("matterport_url")
        if matterport_id and not matterport_url:
            matterport_url = get_matterport_url(matterport_id)

        home_data = {
            "id": h.get("id", ""),
            "model_name": h.get("model_name", "Unknown"),
            "manufacturer": h.get("manufacturer", ""),
            "classification": h.get("classification", ""),
            "status": h.get("status", "Available"),
            "display_price": h.get("pricing", {}).get("display_price", "Call for Price"),
            "price_value": h.get("pricing", {}).get("price_value", 0),
            "specs": h.get("specs", {}),
            "features": h.get("features", [])[:5],
            "image_url": h.get("image_url", ""),
            "gallery_images": gallery_images[:3],
            # New: real property assets from website
            "real_photos": real_photos,
            "image_categories": h.get("image_categories", {}),
            "floor_plan_url": floor_plan_url,
            "matterport_id": matterport_id,
            "matterport_url": matterport_url,
        }

        apply_classifier_to_home(home_data)

        # Try to match this inventory home to scraped website assets
        assets = get_assets_for_home(h.get("model_name", ""))
        if assets:
            asset_images = assets.get("images", [])
            if asset_images and not has_real_photo(home_data):
                existing = home_data.get("real_photos") or home_data.get("gallery_images") or []
                home_data["real_photos"] = [*existing, *asset_images]
                home_data["gallery_images"] = [*existing, *asset_images][:3]
            if assets.get("image_categories") and not home_data["image_categories"]:
                home_data["image_categories"] = assets.get("image_categories", {})
            home_data["floor_plan_url"] = home_data.get("floor_plan_url") or assets.get(
                "floor_plan"
            )
            if assets.get("matterport_id") and not home_data.get("matterport_id"):
                home_data["matterport_id"] = assets["matterport_id"]
                home_data["matterport_url"] = get_matterport_url(assets["matterport_id"])
            apply_classifier_to_home(home_data)

        homes_for_ads.append(home_data)

    # URL-based floorplan classifier: ensure public/marketing consumers only
    # receive real listing photos in image_url/real_photos/gallery_images.
    for home in homes_for_ads:
        apply_classifier_to_home(home)

    # Stats
    total = len(inventory)
    new_count = sum(1 for h in inventory if h.get("status", "").lower() == "available")
    preowned_count = sum(1 for h in inventory if "pre-owned" in h.get("status", "").lower())

    return {
        "success": True,
        # `_load_inventory` is deliberately resilient: Firestore falls back to
        # local JSON and then sample data. Useful for drafting, but not proof of
        # Firestore provenance for an automatic public-source switch.
        "source": "inventory_fallback_chain",
        "homes": homes_for_ads,
        "total_inventory": total,
        "new_homes": new_count,
        "preowned_homes": preowned_count,
        "message": f"Loaded {len(homes_for_ads)} homes from inventory ({total} total).",
    }


# ─── Image Generation with Imagen ───

# Output directory for generated images
GENERATED_ADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "generated_ads"
)
os.makedirs(GENERATED_ADS_DIR, exist_ok=True)

# Style presets for image generation
IMAGE_STYLES = {
    "photorealistic": "professional real estate photography, high resolution, natural lighting, warm tones, inviting atmosphere",
    "modern": "modern minimalist real estate photography, clean lines, bright airy spaces, contemporary design, editorial style",
    "luxury": "luxury real estate photography, premium finishes, elegant staging, golden hour lighting, aspirational lifestyle",
    "cozy": "cozy home photography, warm inviting atmosphere, family-friendly, comfortable living spaces, soft natural light",
    "aerial": "aerial drone photography of manufactured home community, bird's eye view, landscaped lots, Texas countryside",
    "twilight": "twilight exterior real estate photography, warm interior lights glowing, dusk sky, dramatic curb appeal",
}

# Platform aspect ratio defaults
PLATFORM_ASPECT_RATIOS = {
    "tiktok": "9:16",
    "instagram_reels": "9:16",
    "instagram_post": "1:1",
    "facebook": "16:9",
    "facebook_story": "9:16",
}


def generate_ad_image(
    prompt: str,
    home_name: str | None = None,
    platform: str = "tiktok",
    style: str = "photorealistic",
    aspect_ratio: str | None = None,
    tool_context: ToolContext = None,
) -> dict:
    """
    Generate a marketing image for social media ads using Google Imagen.

    Creates professional real estate marketing visuals optimized for
    the target social media platform.

    Args:
        prompt: Description of the image to generate
        home_name: Optional home model name for context
        platform: Target platform (tiktok, instagram_reels, facebook, etc.)
        style: Visual style preset (photorealistic, modern, luxury, cozy, aerial, twilight)
        aspect_ratio: Override aspect ratio (1:1, 9:16, 16:9, 3:4, 4:3)
        tool_context: ADK tool context

    Returns:
        Dictionary with image data (base64), file path, and metadata
    """
    try:
        from google.genai import types
    except Exception as exc:
        return {
            "success": False,
            "error": f"Google GenAI SDK unavailable: {exc}",
            "hint": "Install google-genai and deploy with Vertex AI credentials.",
        }

    client = _genai_client()

    # Determine aspect ratio
    final_aspect = aspect_ratio or PLATFORM_ASPECT_RATIOS.get(platform, "9:16")

    # Build enhanced prompt
    style_desc = IMAGE_STYLES.get(style, IMAGE_STYLES["photorealistic"])

    enhanced_prompt = f"{style_desc}. {prompt}"
    if home_name:
        enhanced_prompt += f". Featured home: {home_name} by Texas Home Outlet."
    enhanced_prompt += " No text overlays, no watermarks, no people."

    model_candidates = [
        os.environ.get("THO_IMAGEN_MODEL", "imagen-4.0-generate-001"),
        *_split_env_list(
            "THO_IMAGEN_FALLBACK_MODELS", "imagen-3.0-generate-002,imagen-3.0-generate-001"
        ),
    ]
    last_error = ""
    try:
        response = None
        model_used = ""
        for model_name in dict.fromkeys(model_candidates):
            try:
                response = client.models.generate_images(
                    model=model_name,
                    prompt=enhanced_prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio=final_aspect,
                        safety_filter_level=types.SafetyFilterLevel.BLOCK_MEDIUM_AND_ABOVE,
                        person_generation=types.PersonGeneration.DONT_ALLOW,
                        include_rai_reason=True,
                    ),
                )
                model_used = model_name
                break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Imagen model %s failed: %s", model_name, exc)
        if response is None:
            raise RuntimeError(last_error or "No Imagen model candidate succeeded")

        if not response.generated_images:
            return {
                "success": False,
                "error": "Image generation returned no results. The prompt may have been filtered for safety. Try adjusting your description.",
                "filtered": True,
            }

        generated = response.generated_images[0]
        image_bytes = generated.image.image_bytes

        # Save to file
        image_id = f"ad_{uuid.uuid4().hex[:8]}_{int(datetime.now().timestamp())}"
        filename = f"{image_id}.png"
        filepath = os.path.join(GENERATED_ADS_DIR, filename)

        with open(filepath, "wb") as f:
            f.write(image_bytes)

        # Base64 encode for frontend display
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        return {
            "success": True,
            "image_id": image_id,
            "image_base64": b64_image,
            "filename": filename,
            "download_url": f"/api/marketing/images/{filename}",
            "aspect_ratio": final_aspect,
            "platform": platform,
            "style": style,
            "prompt_used": enhanced_prompt[:200],
            "home_featured": home_name,
            "model_used": model_used,
            "created_at": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Imagen generation failed: {e}")
        return {
            "success": False,
            **_user_safe_ai_error(e),
            "hint": (
                "Ensure Vertex AI and Imagen access are enabled for the GCP project, "
                "and try a simpler prompt if the request was filtered."
            ),
        }


def _creative_canvas(platform: str) -> tuple[int, int]:
    if platform in {"tiktok", "instagram_reels", "facebook_story"}:
        return 1080, 1920
    if platform == "instagram_post":
        return 1080, 1080
    return 1600, 900


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ]
    for name in names:
        if os.path.exists(name):
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def _wrap_text(draw, text: str, font, max_width: int, max_lines: int = 3) -> list[str]:
    words = str(text or "").split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current.append(word)
            continue
        lines.append(" ".join(current))
        current = [word]
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(" ".join(current))
    if len(lines) == max_lines and words:
        joined = " ".join(lines)
        if len(joined) < len(str(text)):
            lines[-1] = lines[-1].rstrip(" .,") + "..."
    return lines


def _cover_image(image, width: int, height: int):
    ratio = max(width / image.width, height / image.height)
    resized = image.resize((int(image.width * ratio), int(image.height * ratio)))
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _load_source_photo(home_photo_url: str | None = None, image_base64: str | None = None):
    from io import BytesIO

    from PIL import Image

    if image_base64:
        raw = image_base64.split(",", 1)[-1]
        return Image.open(BytesIO(base64.b64decode(raw))).convert("RGB"), "generated_image"
    if home_photo_url:
        import requests

        response = requests.get(home_photo_url, timeout=20)
        response.raise_for_status()
        return Image.open(BytesIO(response.content)).convert("RGB"), "real_photo"
    return None, "branded_fallback"


def generate_ad_flyer(
    home_name: str,
    home_price: str | None = None,
    home_specs: dict | None = None,
    headline: str | None = None,
    body: str | None = None,
    cta: str | None = None,
    platform: str = "instagram_post",
    home_photo_url: str | None = None,
    image_base64: str | None = None,
    tool_context: ToolContext = None,
) -> dict:
    """Create a downloadable social flyer, not just a prompt/phone preview."""
    try:
        from io import BytesIO

        from PIL import Image, ImageDraw, ImageFilter

        width, height = _creative_canvas(platform)
        photo, source = _load_source_photo(home_photo_url=home_photo_url, image_base64=image_base64)
        if photo:
            canvas = _cover_image(photo, width, height).convert("RGBA")
            blur = canvas.filter(ImageFilter.GaussianBlur(radius=18))
            canvas = Image.blend(canvas, blur, 0.10)
        else:
            canvas = Image.new("RGBA", (width, height), "#f4efe7")

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle((0, 0, width, int(height * 0.34)), fill=(42, 22, 18, 118))
        od.rectangle((0, int(height * 0.58), width, height), fill=(42, 22, 18, 178))
        canvas.alpha_composite(overlay)
        draw = ImageDraw.Draw(canvas)

        margin = int(width * 0.075)
        brand_font = _font(max(22, int(width * 0.028)), bold=True)
        headline_font = _font(max(42, int(width * 0.074)), bold=True)
        body_font = _font(max(28, int(width * 0.034)))
        meta_font = _font(max(24, int(width * 0.03)), bold=True)
        cta_font = _font(max(28, int(width * 0.036)), bold=True)
        small_font = _font(max(18, int(width * 0.022)))

        cream = "#fff8ec"
        gold = "#d6a33a"
        burgundy = "#501d1d"
        text_shadow = (0, 0, 0, 145)

        brand = "TEXAS HOME OUTLET"
        draw.text((margin + 2, margin + 2), brand, font=brand_font, fill=text_shadow)
        draw.text((margin, margin), brand, font=brand_font, fill=gold)

        price = home_price or "Call for Price"
        price_text = str(price)
        price_bbox = draw.textbbox((0, 0), price_text, font=meta_font)
        pill_w = price_bbox[2] - price_bbox[0] + 44
        pill_h = price_bbox[3] - price_bbox[1] + 26
        pill_x = width - margin - pill_w
        pill_y = margin - 8
        draw.rounded_rectangle(
            (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h), radius=18, fill=gold
        )
        draw.text((pill_x + 22, pill_y + 12), price_text, font=meta_font, fill=burgundy)

        title = headline or home_name or "Featured Home"
        title_lines = _wrap_text(draw, title, headline_font, width - margin * 2, max_lines=3)
        y = int(height * 0.12)
        for line in title_lines:
            draw.text((margin + 3, y + 3), line, font=headline_font, fill=text_shadow)
            draw.text((margin, y), line, font=headline_font, fill=cream)
            y += int(headline_font.size * 1.08)

        specs = home_specs or {}
        spec_parts = []
        if specs.get("beds"):
            spec_parts.append(f"{specs.get('beds')} Bed")
        if specs.get("baths"):
            spec_parts.append(f"{specs.get('baths')} Bath")
        if specs.get("sq_ft") or specs.get("sqft"):
            spec_parts.append(f"{specs.get('sq_ft') or specs.get('sqft')} Sq Ft")
        if specs.get("dimensions"):
            spec_parts.append(str(specs.get("dimensions")))
        spec_line = "  |  ".join(spec_parts)
        if spec_line:
            draw.text((margin + 2, y + 10), spec_line, font=meta_font, fill=text_shadow)
            draw.text((margin, y + 8), spec_line, font=meta_font, fill=gold)

        body_text = body or "Tour this home at Texas Home Outlet in Houston."
        body_lines = _wrap_text(draw, body_text, body_font, width - margin * 2, max_lines=4)
        y = int(height * 0.68)
        for line in body_lines:
            draw.text((margin + 2, y + 2), line, font=body_font, fill=text_shadow)
            draw.text((margin, y), line, font=body_font, fill=cream)
            y += int(body_font.size * 1.22)

        cta_text = cta or "Call or visit to tour it today"
        footer_top = height - margin - int(cta_font.size * 2.6)
        draw.rounded_rectangle(
            (margin, footer_top, width - margin, height - margin),
            radius=26,
            fill=burgundy,
            outline=gold,
            width=max(3, width // 360),
        )
        draw.text((margin + 28, footer_top + 22), cta_text, font=cta_font, fill=cream)
        draw.text(
            (margin + 28, footer_top + 22 + int(cta_font.size * 1.25)),
            "281-657-7366  |  texashomeoutlet.com",
            font=small_font,
            fill=gold,
        )

        image_id = f"flyer_{uuid.uuid4().hex[:8]}_{int(datetime.now().timestamp())}"
        filename = f"{image_id}.png"
        filepath = os.path.join(GENERATED_ADS_DIR, filename)
        canvas.convert("RGB").save(filepath, format="PNG", optimize=True)

        buf = BytesIO()
        canvas.convert("RGB").save(buf, format="PNG", optimize=True)
        image_data = buf.getvalue()

        return {
            "success": True,
            "creative_type": "social_flyer",
            "image_id": image_id,
            "image_base64": base64.b64encode(image_data).decode("utf-8"),
            "filename": filename,
            "download_url": f"/api/marketing/images/{filename}",
            "platform": platform,
            "resolution": f"{width}x{height}",
            "source": source,
            "home_featured": home_name,
            "created_at": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Flyer generation failed: {e}")
        return {"success": False, **_user_safe_ai_error(e)}


# ─── Content Script Generation ───

# Content categories for manufactured homes
CONTENT_THEMES = [
    "home_tour",
    "myth_busting",
    "financing_tips",
    "before_after",
    "behind_scenes",
    "customer_story",
    "comparison",
    "lifestyle",
    "clearance_alert",
    "faq",
]

# Few-shot examples by content theme — teaches the model what GOOD scripts look like
THEME_EXAMPLES = {
    "home_tour": """EXAMPLE SCRIPT (home_tour):
[SHOT: exterior wide, golden hour] (0:00-0:03)
"$74,900. Three bedrooms, two baths. Let me show you what that buys."

[SHOT: front door opens into living room] (0:03-0:06)
"Open floor plan. Ceilings feel tall because they are — vaulted throughout."

[SHOT: slow pan across kitchen island] (0:06-0:10)
"Full kitchen. Not apartment kitchen — house kitchen. Quartz counters, real cabinet space."

[SHOT: master bedroom, closet door open] (0:10-0:14)
"Master fits a king with room left over. That closet? Walk-in."

[SHOT: back patio view] (0:14-0:18)
"And you own this. No landlord. No shared walls. This is yours."

CTA: "Come see it — link in bio. We're on FM 1960 in Huffman."
""",
    "myth_busting": """EXAMPLE SCRIPT (myth_busting):
[SHOT: you standing outside, skeptical face] (0:00-0:02)
"People say manufactured homes look cheap. Okay, walk with me."

[SHOT: cut to kitchen with pendant lights, island] (0:02-0:06)
"This kitchen has quartz countertops and soft-close cabinets. The same stuff in a $300K stick-built."

[SHOT: bathroom with tiled shower] (0:06-0:09)
"Tiled walk-in shower. Not a plastic insert — actual tile."

[SHOT: exterior curb view] (0:09-0:13)
"From the street, tell me this looks 'cheap.' I'll wait."

[SHOT: price tag graphic overlay] (0:13-0:16)
"$82,000. That's the whole home. Not a down payment — the whole thing."

CTA: "Still think they're cheap? Come see for yourself. Appointments in bio."
""",
    "financing_tips": """EXAMPLE SCRIPT (financing_tips):
[SHOT: sitting at desk, calculator visible] (0:00-0:03)
"$1,400 a month rent. That's $16,800 a year you'll never see again."

[SHOT: home exterior with price overlay] (0:03-0:07)
"This 3 bed/2 bath? $69,900. With FHA, your monthly payment is around $650."

[SHOT: interior living room] (0:07-0:10)
"You keep the equity. You paint the walls. You own it."

[SHOT: close-up, talking directly to camera] (0:10-0:14)
"We have homes starting under $50K. Your credit doesn't have to be perfect — we work with you."

CTA: "DM me 'PAYMENT' and I'll run your numbers for free."
""",
    "clearance_alert": """EXAMPLE SCRIPT (clearance_alert):
[SHOT: lot wide shot, multiple homes visible] (0:00-0:02)
"Three homes just got marked down and nobody's talking about it."

[SHOT: first home exterior] (0:02-0:05)
"This 2 bed/1 bath was $45K. It's $34,900 now."

[SHOT: second home interior, kitchen] (0:05-0:08)
"3 bed/2 bath, full kitchen, pre-owned but clean. $52,000."

[SHOT: walking through third home] (0:08-0:12)
"And this one — just got its price cut today. First person to call gets it."

CTA: "These don't last. Call (281) 324-3020 or link in bio."
""",
    "comparison": """EXAMPLE SCRIPT (comparison):
[SPLIT SCREEN: apartment on left, home on right] (0:00-0:03)
"Left: 2 bed apartment, Houston. $1,400/month. Right: 3 bed home, yours. $680/month."

[SHOT: apartment parking lot] (0:03-0:06)
"Shared parking. Noise upstairs. Can't paint the walls."

[SHOT: home's backyard] (0:06-0:09)
"Your own yard. Your own driveway. Grill whenever you want."

[SHOT: apartment lease paper] (0:09-0:12)
"In 5 years, the apartment costs you $84,000 and you own nothing."

[SHOT: home exterior, proud pose] (0:12-0:15)
"In 5 years, this home? You've built $30K in equity AND your payment went down."

CTA: "Run your numbers — DM me or hit the link."
""",
    "behind_scenes": """EXAMPLE SCRIPT (behind_scenes):
[SHOT: truck arriving with home on flatbed] (0:00-0:03)
"Ever seen a whole house get delivered? Here it comes."

[SHOT: crane setting the home on foundation] (0:03-0:07)
"Built in a factory — climate controlled, no rain delays. Then delivered to your lot."

[SHOT: crew connecting utilities] (0:07-0:10)
"Hook up water, electric, AC. Inspection day is next week."

[SHOT: finished home, family walking in] (0:10-0:14)
"From order to move-in: about 6-8 weeks. Try that with a stick-built."

CTA: "Want to see the process? Come visit the lot. We walk you through everything."
""",
    "customer_story": """EXAMPLE SCRIPT (customer_story):
[SHOT: customer standing in front of their new home] (0:00-0:03)
"Maria was paying $1,500/month rent for a 1-bedroom. Now she owns this."

[SHOT: interior, kids playing in living room] (0:03-0:07)
"3 bedrooms. Her kids each have their own room for the first time."

[SHOT: kitchen, Maria cooking] (0:07-0:10)
"Her mortgage? $720 a month. Less than half her old rent."

[SHOT: Maria smiling at camera] (0:10-0:14)
"She said the hardest part was believing it was real. We hear that a lot."

CTA: "Your story could be next. Let's talk — link in bio."
""",
    "faq": """EXAMPLE SCRIPT (faq):
[SHOT: reading phone, DM visible] (0:00-0:02)
"Number one question in my DMs: 'Do manufactured homes hold value?'"

[SHOT: chart/graphic overlay showing appreciation] (0:02-0:06)
"Short answer: yes. Especially on owned land. Texas manufactured homes appreciated 5-7% last year."

[SHOT: home exterior] (0:06-0:09)
"This isn't 1985. Modern manufactured homes are built to HUD federal code. Same inspections. Real foundations."

[SHOT: close-up, direct to camera] (0:09-0:13)
"The stigma is outdated. The value is real. Come see one in person and tell me I'm wrong."

CTA: "Drop your questions in the comments — I'll answer every one."
""",
}

# Platform-specific formatting
PLATFORM_SPECS = {
    "tiktok": {
        "max_duration": 60,
        "aspect_ratio": "9:16",
        "hashtag_limit": 5,
        "trending_sounds": True,
        "optimal_length": "15-30 seconds",
    },
    "instagram_reels": {
        "max_duration": 90,
        "aspect_ratio": "9:16",
        "hashtag_limit": 30,
        "trending_sounds": True,
        "optimal_length": "15-30 seconds",
    },
    "facebook": {
        "max_duration": 240,
        "aspect_ratio": "16:9 or 9:16",
        "hashtag_limit": 3,
        "trending_sounds": False,
        "optimal_length": "30-60 seconds",
    },
}

# Platform-specific prompt templates
PLATFORM_PROMPTS = {
    "tiktok": """You are creating a TikTok video script. TikTok demands:
- HOOK in first 1-2 seconds (pattern interrupt, bold claim, or surprising visual)
- Fast-paced: 3-5 scene changes in 15-30 seconds
- Conversational, Gen-Z friendly tone (but still professional)
- End with a strong CTA that drives comments/saves
- Include [SHOT] descriptions for visual direction
- Optimal length: 15-30 seconds
- Use trending formats: "POV:", "Things I wish I knew...", "Wait for it...", split-screen comparisons""",
    "instagram_reels": """You are creating an Instagram Reels script. Instagram Reels demands:
- Aesthetic-first: every shot should be visually beautiful
- HOOK that stops the scroll (text overlay + compelling visual)
- Slightly more polished/aspirational tone than TikTok
- Include specific [SHOT] descriptions with lighting/angle notes
- Feature lifestyle elements (not just the home, but the LIFE in the home)
- End with save-worthy CTA ("Save this for your house hunt!")
- Optimal length: 15-30 seconds""",
    "facebook": """You are creating a Facebook video ad script. Facebook demands:
- Longer storytelling format (30-60 seconds)
- Lead with the VALUE PROPOSITION in first 3 seconds
- More informational/educational tone (Facebook audience skews older)
- Include specific pricing, financing details, and location info
- Strong social proof elements (reviews, sales numbers)
- End with clear CTA to call, visit, or message
- Can be more detailed than TikTok/IG""",
}


def _coerce_script_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.extend(str(v) for v in item.values() if v)
            elif item:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(value, dict):
        return "\n".join(str(v) for v in value.values() if v)
    return str(value)


def _normalize_script_shape(script_data) -> dict:
    data = dict(script_data or {}) if isinstance(script_data, dict) else {}
    for field in ("hook", "body", "cta", "duration_estimate", "tone"):
        data[field] = _coerce_script_text(data.get(field))
    prompts = data.get("suggested_image_prompts") or []
    if isinstance(prompts, str):
        prompts = [prompts]
    elif not isinstance(prompts, list):
        prompts = []
    data["suggested_image_prompts"] = [_coerce_script_text(item) for item in prompts if item]
    hashtags = data.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = [tag for tag in hashtags.split() if tag.startswith("#")]
    elif not isinstance(hashtags, list):
        hashtags = []
    data["hashtags"] = [_coerce_script_text(tag) for tag in hashtags if tag]
    return data


def _score_script_quality(
    script_data: dict, home_name: str = None, platform: str = "tiktok"
) -> dict:
    """
    Score a generated script for quality. Returns score breakdown and pass/fail.

    Scoring criteria (1-10 each):
    - hook_strength: Is the hook punchy and pattern-interrupting?
    - specificity: Does it use real numbers, features, room names?
    - authenticity: Does it sound human, not AI-generated?
    - cta_strength: Is the CTA actionable and compelling?
    - structure: Does it have proper [SHOT] markers and timing?

    Pass threshold: average >= 7.0 AND zero banned words.
    """
    import re as _re

    script_data = _normalize_script_shape(script_data)
    hook = _coerce_script_text(script_data.get("hook")).lower()
    body = _coerce_script_text(script_data.get("body")).lower()
    cta = _coerce_script_text(script_data.get("cta")).lower()
    full_text = f"{hook} {body} {cta}"

    scores = {}
    issues = []

    # 1. Hook strength (1-10)
    hook_score = 5
    hook_words = len(hook.split())
    if hook_words <= 12:
        hook_score += 2  # Short hooks are better
    elif hook_words > 20:
        hook_score -= 2
        issues.append("Hook too long — should be under 12 words")
    if _re.search(r"\$[\d,]+", hook):
        hook_score += 2  # Dollar amount in hook = great
    elif any(w in hook for w in ["$", "sqft", "bed", "bath", "%"]):
        hook_score += 1
    if (
        hook.startswith("are you")
        or hook.startswith("have you ever")
        or hook.startswith("what if i told")
    ):
        hook_score -= 4
        issues.append("Hook uses generic question — rewrite with a specific claim")
    if hook.startswith("in today") or hook.startswith("in this video"):
        hook_score -= 3
        issues.append("Hook opens with boring preamble — lead with value")
    if any(f in hook for f in ["pov:", "wait for it", "i'm standing", "nobody's talking"]):
        hook_score += 1
    scores["hook_strength"] = min(10, max(1, hook_score))

    # 2. Specificity (1-10)
    spec_score = 2
    numbers_found = _re.findall(
        r"\$[\d,]+|\d{3,}[\s]?sq|[\d]+\s?bed|[\d]+\s?bath|\d{3,}\s?sqft", full_text
    )
    spec_score += min(4, len(numbers_found))
    if not numbers_found:
        issues.append("No specific numbers — add price, sqft, or bed/bath count")
    if home_name and home_name.lower() in full_text:
        spec_score += 2
    room_words = [
        "kitchen",
        "master",
        "bedroom",
        "bathroom",
        "living room",
        "porch",
        "closet",
        "garage",
        "island",
        "countertop",
        "patio",
        "yard",
        "pantry",
        "laundry",
    ]
    rooms_mentioned = sum(1 for r in room_words if r in full_text)
    spec_score += min(2, rooms_mentioned)
    if rooms_mentioned == 0:
        issues.append("No rooms or features mentioned — reference actual spaces")
    scores["specificity"] = min(10, max(1, spec_score))

    # 3. Authenticity (1-10) — penalize AI slop hard
    auth_score = 9
    banned_found = [w for w in BANNED_WORDS if w in full_text]
    auth_score -= len(banned_found) * 2
    if banned_found:
        issues.append(f"Banned words: {', '.join(banned_found[:5])}")
    excl_count = full_text.count("!")
    if excl_count > 3:
        auth_score -= min(3, excl_count - 2)
        issues.append(f"{excl_count} exclamation marks — max 2 for authenticity")
    emoji_count = len(_re.findall(r"[\U0001f300-\U0001f9ff]", full_text))
    if emoji_count > 2:
        auth_score -= 1
        issues.append("Too many emojis — max 1 for video scripts")
    sentences = _re.split(r"[.!?\n]", body)
    long_sentences = [s for s in sentences if len(s.split()) > 25]
    if len(long_sentences) > 1:
        auth_score -= 1
        issues.append("Long run-on sentences — break into short visual beats")
    scores["authenticity"] = min(10, max(1, auth_score))

    # 4. CTA strength (1-10)
    cta_score = 5
    action_words = [
        "call",
        "visit",
        "text",
        "dm",
        "comment",
        "save",
        "link",
        "bio",
        "tap",
        "click",
        "book",
        "schedule",
    ]
    if any(w in cta for w in action_words):
        cta_score += 3
    else:
        issues.append("CTA needs a clear action verb (call, DM, link in bio)")
    if "?" in cta:
        cta_score += 1
    if len(cta.split()) < 3:
        cta_score -= 2
        issues.append("CTA too short — needs clear action")
    if any(w in cta for w in ["281", "fm 1960", "huffman", "houston"]):
        cta_score += 1
    scores["cta_strength"] = min(10, max(1, cta_score))

    # 5. Structure (1-10) — video-ready formatting
    struct_score = 4
    shot_markers = _re.findall(r"\[SHOT[:\s]", body, _re.IGNORECASE)
    timing_markers = _re.findall(r"\(\d+:\d+", body)
    if len(shot_markers) >= 3:
        struct_score += 3
    elif len(shot_markers) >= 1:
        struct_score += 1
    else:
        issues.append("No [SHOT] markers — add visual direction for each scene")
    if len(timing_markers) >= 2:
        struct_score += 2
    elif len(timing_markers) == 0 and platform != "facebook":
        issues.append("No timing markers — add (0:00-0:03) style pacing")
    body_words = len(body.split())
    if platform == "tiktok" and body_words > 150:
        struct_score -= 1
        issues.append("Too long for TikTok — trim to under 100 words")
    elif platform == "facebook" and body_words < 30:
        struct_score -= 1
        issues.append("Too short for Facebook — expand with more detail")
    scores["structure"] = min(10, max(1, struct_score))

    # Total — raised bar: 7.0 to pass
    total = sum(scores.values())
    avg = total / len(scores)
    passed = avg >= 7.0 and len(banned_found) == 0

    return {
        "scores": scores,
        "average": round(avg, 1),
        "total": total,
        "max_possible": len(scores) * 10,
        "passed": passed,
        "issues": issues,
        "banned_words_found": banned_found,
    }


def _refine_script_if_needed(
    client, model_name, script_data: dict, quality: dict, platform: str
) -> dict:
    """
    Second pass: If quality score is too low, send script back for refinement.
    Returns the refined script data or original if already good.
    """
    from google.genai import types

    if quality["passed"]:
        return script_data  # Already good

    issues_str = "\n".join(f"- {issue}" for issue in quality["issues"])
    banned_str = (
        ", ".join(quality["banned_words_found"]) if quality["banned_words_found"] else "none"
    )

    refine_prompt = f"""You are a script editor for Texas Home Outlet. Review and IMPROVE this script.

ORIGINAL SCRIPT:
Hook: {script_data.get('hook', '')}
Body: {script_data.get('body', '')}
CTA: {script_data.get('cta', '')}

QUALITY ISSUES FOUND:
{issues_str}

BANNED WORDS USED (MUST REMOVE): {banned_str}

RULES FOR IMPROVEMENT:
- Fix all quality issues listed above
- Remove ALL banned words and replace with natural alternatives
- Keep the same general structure and intent
- Make it sound like a real person talking, not AI
- Maintain specific numbers and home details
- Keep the hook under 10 words

Return ONLY the improved script as JSON:
{{"hook": "...", "body": "...", "cta": "...", "hashtags": {json.dumps(script_data.get('hashtags', []))}, "duration_estimate": "{script_data.get('duration_estimate', '30s')}", "suggested_image_prompts": {json.dumps(script_data.get('suggested_image_prompts', []))}, "tone": "{script_data.get('tone', 'authentic')}"}}
"""

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=refine_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.6,
            ),
        )
        refined = json.loads(response.text)
        if isinstance(refined, list):
            refined = refined[0] if refined else script_data
        return refined
    except Exception as e:
        logger.warning(f"Script refinement failed, using original: {e}")
        return script_data


def generate_content_script(
    home_id: str | None = None,
    home_name: str | None = None,
    home_price: str | None = None,
    home_specs: dict | None = None,
    content_theme: str = "home_tour",
    platform: str = "tiktok",
    custom_hook: str | None = None,
    language: str = "en",
    avatar: str = "tex_classic",
    custom_avatar_prompt: str | None = None,
    variations: int = 1,
    tool_context: ToolContext = None,
) -> dict:
    """
    Generate viral-ready video scripts for social media using Gemini.

    Enhanced with:
    - Platform-specific prompting (TikTok vs Instagram vs Facebook)
    - Real inventory context for accurate home details
    - A/B script variations
    - Suggested Imagen prompts for visual assets
    - Few-shot examples of viral manufactured home content

    Args:
        home_id: Inventory ID of home to feature
        home_name: Home model name
        home_price: Display price
        home_specs: Home specifications dict
        content_theme: Content type (home_tour, myth_busting, etc.)
        platform: Target platform (tiktok, instagram_reels, facebook)
        custom_hook: Custom opening hook override
        language: Script language (en, es)
        avatar: Presenter avatar style
        custom_avatar_prompt: Custom avatar description
        variations: Number of script variations (1-3)
        tool_context: ADK tool context

    Returns:
        Dictionary with script(s), hashtags, image prompts, and metadata
    """
    try:
        from google.genai import types
    except Exception as exc:
        return {
            "success": False,
            "error": f"Google GenAI SDK unavailable: {exc}",
            "status": "gcp_not_ready",
        }

    client = _genai_client()

    # Clamp variations
    variations = max(1, min(3, variations))

    # Avatar context
    avatar_desc = {
        "tex_classic": "Classic Tex: A friendly, warm Texas cowboy-themed presenter. Approachable, trustworthy, with a bit of Southern charm.",
        "tex_modern": "Modern Tex: A sleek, professional real estate expert. Data-driven, confident, tech-savvy.",
        "tex_custom": custom_avatar_prompt or "A personalized AI presenter.",
    }.get(avatar, "Classic Tex")

    # ─── Load real property assets for photo-backed scripts ───
    try:
        from tools.asset_scraper import get_assets_for_home, get_matterport_url
    except ImportError:
        from .asset_scraper import get_assets_for_home, get_matterport_url

    real_photos = []
    matterport_context = ""
    photo_context = ""
    assets = None

    if home_name:
        assets = get_assets_for_home(home_name)
        if assets:
            real_photos = assets.get("images", [])
            if assets.get("matterport_id"):
                matterport_context = f"\n3D TOUR AVAILABLE: https://my.matterport.com/show/?m={assets['matterport_id']}&play=1\nMention the 3D tour in the CTA — viewers can walk through this home from their phone!"
            if real_photos:
                photo_labels = [
                    f"  Photo {i+1}: {url.split('/')[-1]}" for i, url in enumerate(real_photos[:6])
                ]
                photo_context = f"""
REAL PROPERTY PHOTOS AVAILABLE ({len(real_photos)} photos):
{chr(10).join(photo_labels)}

Your [SHOT] descriptions should reference these ACTUAL photos. When you write [SHOT: kitchen],
the viewer will see the REAL kitchen from these photos, not a stock image.
"""

    # ─── Build inventory context ───
    inventory_context = ""
    if not home_name:
        # Auto-load inventory highlights for context
        inv = _load_inventory_for_marketing()
        if inv:
            featured = [h for h in inv if h.get("pricing", {}).get("price_value", 0) > 0][:3]
            if featured:
                inventory_lines = []
                for h in featured:
                    specs = h.get("specs", {})
                    inventory_lines.append(
                        f"- {h.get('model_name', 'Home')} by {h.get('manufacturer', 'THO')}: "
                        f"{specs.get('beds', '?')}BR/{specs.get('baths', '?')}BA, "
                        f"{specs.get('sq_ft', '?')} sqft, "
                        f"{h.get('pricing', {}).get('display_price', 'Call for Price')}"
                    )
                inventory_context = f"""
CURRENT INVENTORY HIGHLIGHTS (use these real details in the script):
{chr(10).join(inventory_lines)}

Pick the most compelling home for this {content_theme} theme, or reference multiple homes for comparison content.
"""
    else:
        inventory_context = f"""
FEATURED HOME:
- Name: {home_name}
- Price: {home_price or 'Call for Price'}
- Specs: {json.dumps(home_specs or {})}
{photo_context}
{matterport_context}

Use these EXACT details in the script. Do not make up specifications.
"""

    # ─── Build platform-specific prompt ───
    platform_guidance = PLATFORM_PROMPTS.get(platform, PLATFORM_PROMPTS["tiktok"])

    # ─── Inject theme-specific few-shot example ───
    theme_example = THEME_EXAMPLES.get(content_theme, "")
    theme_example_section = ""
    if theme_example:
        theme_example_section = f"""
HERE IS AN EXAMPLE OF A HIGH-QUALITY {content_theme.upper().replace('_', ' ')} SCRIPT.
Study its structure, pacing, shot direction, and tone. Match this quality level.
Do NOT copy it — write something original using the FEATURED HOME details above.

{theme_example}
"""

    variation_instruction = ""
    if variations > 1:
        variation_instruction = f"""
Generate {variations} COMPLETELY DIFFERENT script variations. Rules:
- Each MUST use a different hook style (e.g., V1: bold price claim, V2: "POV:" format, V3: myth-busting question)
- Each MUST take a different emotional angle (e.g., V1: logical/numbers-driven, V2: aspirational/emotional, V3: funny/relatable)
- Each should feel like it was written by a different creator, not just the same script reworded
- Vary sentence length and pacing across variations
- ALL variations must still include specific numbers and home details

Return them as a JSON array of {variations} script objects.
"""

    # Build JSON format template separately to avoid f-string escaping issues
    script_obj_template = (
        '    "hook": "Pattern-interrupt opening (< 8 words)",\n'
        '    "body": "Full script with [SHOT: description] markers for each visual change. '
        'Include timing notes like (0:00-0:03), (0:03-0:08), etc.",\n'
        '    "cta": "Call to action that drives engagement",\n'
        f'    "hashtags": ["5-8 relevant hashtags for {platform}"],\n'
        '    "duration_estimate": "XX-XX seconds",\n'
        '    "suggested_image_prompts": [\n'
        '      "Imagen prompt 1: describe a key visual from this script for image generation",\n'
        '      "Imagen prompt 2: describe another compelling visual"\n'
        "    ],\n"
        '    "tone": "one-word tone descriptor"'
    )

    if variations > 1:
        json_format = f"[\n  {{\n{script_obj_template},\n"
        json_format += (
            '    "tone": "one-word tone descriptor (e.g., exciting, informative, emotional)"\n'
        )
        json_format += "  }\n]"
    else:
        json_format = f"{{\n{script_obj_template}\n}}"

    # ─── Build banned words list for prompt ───
    banned_words_str = ", ".join(BANNED_WORDS)

    prompt = f"""You are Tex, the AI content creator for Texas Home Outlet — a manufactured home dealership in Houston, TX.

{THO_BRAND_VOICE}

ABSOLUTELY BANNED WORDS/PHRASES (never use ANY of these):
{banned_words_str}

PLATFORM GUIDELINES:
{platform_guidance}

PRESENTER STYLE: {avatar_desc}
CONTENT THEME: {content_theme}
LANGUAGE: {"Spanish (respond ENTIRELY in Spanish)" if language == "es" else "English"}
{inventory_context}
CUSTOM HOOK: {custom_hook or "Generate your own viral hook"}

{theme_example_section}
{variation_instruction}

VIRAL CONTENT PRINCIPLES FOR MANUFACTURED HOMES:
1. Address the #1 objection: "Manufactured homes are cheap/ugly" — PROVE them wrong
2. Lead with the surprising value prop: "Own for less than rent"
3. Show specific numbers: listing prices, sqft, bedrooms
4. Use comparison framing: apartment vs. home ownership
5. Create FOMO: "This home won't last at this price"
6. End with engagement hooks: questions, polls, "comment if..."
7. NEVER start with "Are you looking for..." or "Have you ever dreamed of..."
8. EVERY script MUST include at least one specific number (price, sqft, beds/baths)

Output as JSON format:
{json_format}
"""

    try:
        # Try the current stable Flash model first, then configured fallbacks.
        # Temperature 0.7 keeps output lively while the brand prompt controls quality.
        model_candidates = [
            os.environ.get("THO_GEMINI_MODEL", "gemini-2.5-flash"),
            *_split_env_list("THO_GEMINI_FALLBACK_MODELS", "gemini-2.0-flash-001"),
        ]
        response = None
        model_name = ""
        last_error = ""
        for candidate in dict.fromkeys(model_candidates):
            try:
                response = client.models.generate_content(
                    model=candidate,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.7,
                    ),
                )
                model_name = candidate
                break
            except Exception as exc:
                last_error = str(exc)
                logger.warning("Gemini model %s failed: %s", candidate, exc)
        if response is None:
            raise RuntimeError(last_error or "No Gemini model candidate succeeded")

        data = json.loads(response.text)
        script_id = f"SCRIPT-{uuid.uuid4().hex[:6].upper()}"

        # ─── Two-pass quality scoring & refinement ───
        def _process_script(item):
            """Score, refine if needed, and return processed script with quality data."""
            item = _normalize_script_shape(item)
            quality = _score_script_quality(item, home_name, platform)
            if not quality["passed"]:
                logger.info(
                    f"Script quality {quality['average']}/10 — refining (issues: {quality['issues']})"
                )
                refined = _refine_script_if_needed(client, model_name, item, quality, platform)
                refined = _normalize_script_shape(refined)
                # Re-score the refined version
                quality = _score_script_quality(refined, home_name, platform)
                return refined, quality
            return item, quality

        # Handle single vs multiple variations
        if variations > 1 and isinstance(data, list):
            scripts = []
            quality_scores = []
            for i, item in enumerate(data[:variations]):
                processed, quality = _process_script(item)
                quality_scores.append(quality)
                scripts.append(
                    {
                        "variation": i + 1,
                        "hook": processed.get("hook", ""),
                        "body": processed.get("body", ""),
                        "cta": processed.get("cta", ""),
                        "duration_estimate": processed.get("duration_estimate", "30s"),
                        "tone": processed.get("tone", ""),
                        "suggested_image_prompts": processed.get("suggested_image_prompts", []),
                        "quality_score": quality["average"],
                    }
                )
            hashtags = data[0].get("hashtags", []) if data else []

            return {
                "success": True,
                "script_id": script_id,
                "platform": platform,
                "content_theme": content_theme,
                "language": language,
                "avatar": avatar,
                "model_used": model_name,
                "variations": len(scripts),
                "scripts": scripts,
                "script": scripts[0] if scripts else None,
                "hashtags": hashtags,
                "platform_specs": PLATFORM_SPECS.get(platform, {}),
                "home_featured": home_name,
                "real_photos": real_photos,
                "image_categories": assets.get("image_categories", {})
                if (home_name and assets)
                else {},
                "matterport_url": get_matterport_url(assets["matterport_id"])
                if (home_name and assets and assets.get("matterport_id"))
                else None,
                "matterport_id": assets.get("matterport_id") if (home_name and assets) else None,
                "quality_scores": [q["average"] for q in quality_scores],
                "quality_details": quality_scores,
                "created_at": datetime.now().isoformat(),
                "status": "ready_for_production",
            }
        else:
            # Single script (or array with one item)
            if isinstance(data, list):
                data = data[0] if data else {}

            processed, quality = _process_script(data)

            return {
                "success": True,
                "script_id": script_id,
                "platform": platform,
                "content_theme": content_theme,
                "language": language,
                "avatar": avatar,
                "model_used": model_name,
                "variations": 1,
                "script": {
                    "hook": processed.get("hook", ""),
                    "body": processed.get("body", ""),
                    "cta": processed.get("cta", ""),
                    "duration_estimate": processed.get("duration_estimate", "30s"),
                    "tone": processed.get("tone", ""),
                    "suggested_image_prompts": processed.get("suggested_image_prompts", []),
                    "quality_score": quality["average"],
                },
                "hashtags": processed.get("hashtags", data.get("hashtags", [])),
                "platform_specs": PLATFORM_SPECS.get(platform, {}),
                "home_featured": home_name,
                "real_photos": real_photos,
                "image_categories": assets.get("image_categories", {})
                if (home_name and assets)
                else {},
                "matterport_url": get_matterport_url(assets["matterport_id"])
                if (home_name and assets and assets.get("matterport_id"))
                else None,
                "matterport_id": assets.get("matterport_id") if (home_name and assets) else None,
                "quality": quality,
                "created_at": datetime.now().isoformat(),
                "status": "ready_for_production",
            }
    except Exception as e:
        logger.error(f"AI Generation failed: {e}")
        return {"success": False, "status": "error", **_user_safe_ai_error(e)}


def get_trending_content_ideas(
    inventory_highlights: list[dict] | None = None,
    recent_sales: int | None = None,
    current_promotions: list[str] | None = None,
    tool_context: ToolContext = None,
) -> dict:
    """
    Generate trending content ideas based on real inventory and market trends.

    Args:
        inventory_highlights: List of featured homes to promote
        recent_sales: Number of recent sales for social proof content
        current_promotions: Active promotions (Red Tag, Year End, etc.)
        tool_context: ADK tool context

    Returns:
        Dictionary with content calendar ideas
    """
    ideas = []

    # Load real inventory for personalized ideas
    inventory = _load_inventory_for_marketing()

    # Featured home of the week (pick most expensive/impressive)
    if inventory:
        priced = [h for h in inventory if h.get("pricing", {}).get("price_value", 0) > 0]
        priced.sort(key=lambda x: x["pricing"]["price_value"], reverse=True)

        if priced:
            top_home = priced[0]
            specs = top_home.get("specs", {})
            ideas.append(
                {
                    "type": "home_tour",
                    "title": f"Home Tour: {top_home.get('model_name', 'Featured Home')} — {top_home.get('pricing', {}).get('display_price', '')}",
                    "platform_priority": ["tiktok", "instagram_reels", "facebook"],
                    "trending_potential": "very high",
                    "notes": f"{specs.get('beds', '?')}BR/{specs.get('baths', '?')}BA, {specs.get('sq_ft', '?')} sqft by {top_home.get('manufacturer', 'THO')}. Tour the best-looking model on the lot!",
                    "home_id": top_home.get("id"),
                    "home_name": top_home.get("model_name"),
                }
            )

        # Budget-friendly option
        cheapest = priced[-1] if priced else None
        if cheapest and cheapest != (priced[0] if priced else None):
            ideas.append(
                {
                    "type": "financing_tips",
                    "title": f"Own a Home for {cheapest.get('pricing', {}).get('display_price', '')}!",
                    "platform_priority": ["tiktok", "facebook"],
                    "trending_potential": "high",
                    "notes": f"Show the {cheapest.get('model_name', '')} and highlight the affordable price point. Apartment vs ownership comparison.",
                    "home_id": cheapest.get("id"),
                    "home_name": cheapest.get("model_name"),
                }
            )

        # Pre-owned deals
        preowned = [h for h in inventory if "pre-owned" in h.get("status", "").lower()]
        if preowned:
            ideas.append(
                {
                    "type": "clearance_alert",
                    "title": f"Pre-Owned Homes Starting Under $30K! ({len(preowned)} Available)",
                    "platform_priority": ["tiktok", "facebook"],
                    "trending_potential": "very high",
                    "notes": "Budget homes are VIRAL. Show the value — home ownership for less than a used car.",
                    "home_count": len(preowned),
                }
            )

    # Staple content (always include)
    staple_content = [
        {
            "type": "myth_busting",
            "title": "Mobile Home Myths DEBUNKED",
            "platform_priority": ["tiktok", "instagram_reels"],
            "trending_potential": "high",
            "notes": "These always perform well — people love being proven wrong. Show modern interiors.",
        },
        {
            "type": "comparison",
            "title": "Apartment vs Own This Home (Same Price!)",
            "platform_priority": ["tiktok"],
            "trending_potential": "very high",
            "notes": "Side-by-side comparisons are VIRAL. Use split-screen format with real numbers.",
        },
        {
            "type": "behind_scenes",
            "title": "Watch This Home Get Delivered",
            "platform_priority": ["tiktok", "instagram_reels"],
            "trending_potential": "high",
            "notes": "Behind-the-scenes process content performs very well. Show the delivery and setup.",
        },
        {
            "type": "faq",
            "title": "Answering Your DMs: Top 5 Questions",
            "platform_priority": ["tiktok", "instagram_reels"],
            "trending_potential": "medium",
            "notes": "Builds engagement and answers objections proactively.",
        },
    ]
    ideas.extend(staple_content)

    # If there are clearance promotions
    if current_promotions:
        for promo in current_promotions[:2]:
            ideas.append(
                {
                    "type": "clearance_alert",
                    "title": f"{promo} — Limited Time",
                    "platform_priority": ["tiktok", "facebook"],
                    "trending_potential": "high",
                    "notes": "Urgency content drives immediate engagement and DMs.",
                }
            )

    # Social proof
    if recent_sales and recent_sales > 0:
        ideas.append(
            {
                "type": "customer_story",
                "title": f"We helped {recent_sales} families this month!",
                "platform_priority": ["facebook", "instagram_reels"],
                "trending_potential": "medium",
                "notes": "Social proof builds trust — ask for customer video testimonials.",
            }
        )

    return {
        "success": True,
        "content_ideas": ideas,
        "recommended_posting_schedule": {
            "tiktok": "1-2x daily for maximum reach",
            "instagram_reels": "1x daily",
            "facebook": "1x daily, boost top performers",
        },
        "inventory_loaded": len(inventory) > 0,
        "inventory_count": len(inventory),
        "top_priority": ideas[0] if ideas else None,
        "generated_at": datetime.now().isoformat(),
    }


# ─── Social Media Posting ───


class TikTokHandler:
    """Handles interactions with TikTok for Business API."""

    def __init__(self):
        self.access_token = os.environ.get("TIKTOK_ACCESS_TOKEN")
        self.advertiser_id = os.environ.get("TIKTOK_ADVERTISER_ID")
        self.base_url = "https://business-api.tiktok.com/open_api/v1.3"

    def is_configured(self):
        return bool(self.access_token and self.advertiser_id)

    def post_video(self, video_url: str, caption: str, privacy_level: str = "PUBLIC_TO_EVERYONE"):
        if not self.is_configured():
            return {"success": False, "error": "TikTok credentials not configured"}

        try:
            # Mocked for safety until keys are configured
            return {
                "success": True,
                "message": "Request sent to TikTok API (Simulated)",
                "post_id": f"TT-{uuid.uuid4().hex[:8]}",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


tiktok_handler = TikTokHandler()


def schedule_social_post(
    platform: str,
    content_type: str,
    script_id: str | None = None,
    post_time: str | None = None,
    caption: str | None = None,
    hashtags: list[str] | None = None,
    video_url: str | None = None,
    home_name: str | None = None,
    campaign: str | None = None,
    tool_context: ToolContext = None,
) -> dict:
    """
    Prepare a social post draft. This tool never publishes.
    """
    scheduled_time = post_time or datetime.now().isoformat()
    try:
        from tools.social_publishers import prepare_or_publish_social_post
    except ImportError:
        from .social_publishers import prepare_or_publish_social_post

    result = prepare_or_publish_social_post(
        platform=platform,
        content_type=content_type,
        scheduled_time=scheduled_time,
        caption=caption or "",
        hashtags=hashtags or [],
        video_url=video_url,
        # utm_campaign source: explicit campaign wins, else the featured home/plan.
        campaign=campaign or home_name,
    )

    optimal_times = {
        "tiktok": ["7:00 AM", "12:00 PM", "7:00 PM", "10:00 PM"],
        "instagram": ["9:00 AM", "12:00 PM", "5:00 PM"],
        "instagram_reels": ["9:00 AM", "12:00 PM", "5:00 PM"],
        "facebook": ["9:00 AM", "1:00 PM", "4:00 PM"],
    }

    return {
        **result,
        "script_reference": script_id,
        "optimal_times": optimal_times.get(platform, []),
        "tip": f"For {platform}, best engagement is typically at {optimal_times.get(platform, ['varies'])[0]} CST",
    }


def analyze_content_performance(
    post_ids: list[str] | None = None, date_range: str = "7d", tool_context: ToolContext = None
) -> dict:
    """
    Analyze performance of recent social media content.
    """
    inventory = _load_inventory_for_marketing()
    preowned_count = len([h for h in inventory if "pre-owned" in h.get("status", "").lower()])
    try:
        from tools.photo_classifier import has_real_photo
    except ImportError:
        from .photo_classifier import has_real_photo

    photo_ready_count = len([h for h in inventory if has_real_photo(h)])

    def count_generated(directory: str, extensions: tuple[str, ...]) -> int:
        try:
            return sum(
                1
                for item in os.scandir(directory)
                if item.is_file() and item.name.lower().endswith(extensions)
            )
        except OSError:
            return 0

    video_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "generated_videos")
    generated_images = count_generated(GENERATED_ADS_DIR, (".png", ".jpg", ".jpeg", ".webp"))
    generated_videos = count_generated(video_dir, (".mp4", ".mov", ".webm"))

    try:
        from tools.social_publishers import social_readiness
    except ImportError:
        from .social_publishers import social_readiness

    readiness = social_readiness()
    connected = any(
        platform.get("configured") and readiness.get("publish_enabled")
        for platform in readiness.get("platforms", {}).values()
    )
    top_content = []
    if generated_videos:
        top_content.append(
            {
                "type": "video_assets_ready",
                "views": generated_videos,
                "views_label": f"{generated_videos} generated videos",
                "engagement_rate": "local",
                "engagement_label": "pending publish data",
            }
        )
    if generated_images:
        top_content.append(
            {
                "type": "image_creatives_ready",
                "views": generated_images,
                "views_label": f"{generated_images} generated images",
                "engagement_rate": "local",
                "engagement_label": "pending publish data",
            }
        )
    top_content.append(
        {
            "type": "inventory_backed_ideas",
            "views": len(inventory),
            "views_label": f"{len(inventory)} homes available",
            "engagement_rate": "ready",
            "engagement_label": f"{photo_ready_count} with photos",
        }
    )

    recommendations = [
        "Connect TikTok or Meta analytics before treating reach, follower, or DM metrics as live.",
        f"Use the inventory-backed idea lane for {len(inventory)} current homes.",
    ]
    if photo_ready_count:
        recommendations.append(
            f"Prioritize the {photo_ready_count} homes with usable photos for fastest video creation."
        )
    if preowned_count:
        recommendations.append(
            f"Feature the {preowned_count} pre-owned homes as the budget-friendly content lane."
        )

    return {
        "success": True,
        "date_range": date_range,
        "source": "api_connected" if connected else "local_readiness",
        "social_analytics_connected": connected,
        "social_readiness": readiness,
        "disclaimer": None
        if connected
        else (
            "Live social-platform analytics are not connected; showing local creative readiness instead."
        ),
        "summary": {
            "total_views": 0,
            "total_engagement": 0,
            "new_followers": 0,
            "dms_received": 0,
            "leads_generated": 0,
            "generated_images": generated_images,
            "generated_videos": generated_videos,
            "inventory_count": len(inventory),
            "photo_ready_homes": photo_ready_count,
        },
        "top_performing_content": top_content[:3],
        "recommendations": recommendations,
        "generated_at": datetime.now().isoformat(),
    }


# ─── Text-to-Speech for Voiceover Generation (Google Cloud) ───


def generate_script_voiceover(
    script_text: str,
    voice: str = "en-US-Neural2-D",
    speaking_rate: float = 1.0,
    tool_context: ToolContext = None,
) -> dict:
    """
    Generate voiceover audio from a script using Google Cloud Text-to-Speech.
    Uses Neural2 and Studio-quality voices.

    Args:
        script_text: The script to convert to speech (hook + body + cta)
        voice: Voice name (e.g., en-US-Neural2-D, en-US-Studio-O)
        speaking_rate: Speed of speech (0.25 to 4.0, default 1.0)

    Returns:
        Dict with base64-encoded MP3 audio and metadata
    """
    try:
        from google.cloud import texttospeech

        # Initialize client (uses ADC on Cloud Run)
        client = texttospeech.TextToSpeechClient()

        # Clean up script text for TTS
        clean_text = script_text
        import re

        clean_text = re.sub(r"\[SHOT:[^\]]*\]", "", clean_text)
        clean_text = re.sub(r"\(\d+:\d+[^\)]*\)", "", clean_text)
        clean_text = re.sub(r"\n+", " ", clean_text)
        clean_text = " ".join(clean_text.split())

        if len(clean_text) > 5000:
            clean_text = clean_text[:5000]  # Google TTS limit

        if len(clean_text) < 10:
            return {
                "success": False,
                "error": "Script text too short for voiceover (need at least 10 characters)",
            }

        # Set up the voice and audio config
        voice_params = texttospeech.VoiceSelectionParams(language_code="en-US", name=voice)

        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=speaking_rate,
            pitch=0.0,
            volume_gain_db=0.0,
        )

        # Synthesize speech
        synthesis_input = texttospeech.SynthesisInput(text=clean_text)
        response = client.synthesize_speech(
            input=synthesis_input, voice=voice_params, audio_config=audio_config
        )

        # Encode audio to base64
        audio_base64 = base64.b64encode(response.audio_content).decode("utf-8")

        # Estimate duration (rough: ~150 words per minute at 1.0 rate)
        word_count = len(clean_text.split())
        duration_seconds = int((word_count / 150) * 60 / speaking_rate)

        return {
            "success": True,
            "audio_base64": audio_base64,
            "filename": f"voiceover_{uuid.uuid4().hex[:8]}.mp3",
            "voice": voice,
            "provider": "google-cloud-tts",
            "word_count": word_count,
            "estimated_duration_seconds": duration_seconds,
            "content_preview": clean_text[:100] + "..." if len(clean_text) > 100 else clean_text,
            "generated_at": __import__("datetime").datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Voiceover generation failed: {e}")
        return {
            "success": False,
            **_user_safe_ai_error(e),
            "setup_instructions": "Ensure GOOGLE_CLOUD_PROJECT is set and Text-to-Speech API is enabled.",
        }


# Google Cloud TTS Voice options for frontend
TTS_VOICES = [
    # Neural2 Voices - High quality, natural sounding
    {
        "id": "en-US-Neural2-A",
        "name": "Neural2-A",
        "description": "Female, professional",
        "style": "Conversational",
        "tier": "Neural2",
    },
    {
        "id": "en-US-Neural2-C",
        "name": "Neural2-C",
        "description": "Male, professional",
        "style": "Conversational",
        "tier": "Neural2",
    },
    {
        "id": "en-US-Neural2-D",
        "name": "Neural2-D",
        "description": "Male, warm",
        "style": "Friendly",
        "tier": "Neural2",
    },
    {
        "id": "en-US-Neural2-E",
        "name": "Neural2-E",
        "description": "Female, upbeat",
        "style": "Energetic",
        "tier": "Neural2",
    },
    {
        "id": "en-US-Neural2-F",
        "name": "Neural2-F",
        "description": "Female, clear",
        "style": "Professional",
        "tier": "Neural2",
    },
    {
        "id": "en-US-Neural2-G",
        "name": "Neural2-G",
        "description": "Female, warm",
        "style": "Friendly",
        "tier": "Neural2",
    },
    {
        "id": "en-US-Neural2-H",
        "name": "Neural2-H",
        "description": "Female, calm",
        "style": "Narrative",
        "tier": "Neural2",
    },
    {
        "id": "en-US-Neural2-I",
        "name": "Neural2-I",
        "description": "Male, authoritative",
        "style": "Professional",
        "tier": "Neural2",
    },
    {
        "id": "en-US-Neural2-J",
        "name": "Neural2-J",
        "description": "Male, casual",
        "style": "Conversational",
        "tier": "Neural2",
    },
    # Studio Voices - Broadcast quality
    {
        "id": "en-US-Studio-O",
        "name": "Studio-O",
        "description": "Female, broadcast",
        "style": "Professional",
        "tier": "Studio",
    },
    {
        "id": "en-US-Studio-Q",
        "name": "Studio-Q",
        "description": "Male, broadcast",
        "style": "Professional",
        "tier": "Studio",
    },
    # News Voices - Optimized for news content
    {
        "id": "en-US-News-K",
        "name": "News-K",
        "description": "Female, news anchor",
        "style": "Authoritative",
        "tier": "News",
    },
    {
        "id": "en-US-News-L",
        "name": "News-L",
        "description": "Male, news anchor",
        "style": "Authoritative",
        "tier": "News",
    },
    # Wavenet Voices - Legacy but still good
    {
        "id": "en-US-Wavenet-D",
        "name": "Wavenet-D",
        "description": "Male, warm",
        "style": "Friendly",
        "tier": "Wavenet",
    },
    {
        "id": "en-US-Wavenet-E",
        "name": "Wavenet-E",
        "description": "Female, clear",
        "style": "Professional",
        "tier": "Wavenet",
    },
]
