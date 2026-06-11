"""Prompt template loader — agent instructions as editable content, not code.

Templates live in prompts/*.md and use string.Template `$variable` syntax
(JSON braces need no escaping; literal `$` is written `$$`). The standard
variable set comes from config.yaml via build_context(); callers may add or
override variables per render.

Set THO_PROMPT_DIR to load templates from another directory (per-file
fallback to the repo prompts/ dir) — prompt experiments without a deploy.

Unresolved variables raise at render time so a typo can't silently ship a
prompt with a dangling `$placeholder`.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from string import Template
from zoneinfo import ZoneInfo

from config_loader import (
    business_address,
    business_hours,
    business_name,
    business_phone,
    get_agent_config,
    get_product_config,
    product_plural,
    product_singular,
)

_REPO_PROMPT_DIR = Path(__file__).parent / "prompts"

# mtime-keyed cache: name -> (path, mtime, raw template text)
_template_cache: dict[str, tuple[Path, float, str]] = {}


def _prompt_path(name: str) -> Path:
    filename = f"{name}.md"
    override_dir = os.environ.get("THO_PROMPT_DIR")
    if override_dir:
        candidate = Path(override_dir) / filename
        if candidate.is_file():
            return candidate
    return _REPO_PROMPT_DIR / filename


def _load_template(name: str) -> str:
    path = _prompt_path(name)
    mtime = path.stat().st_mtime
    cached = _template_cache.get(name)
    if cached and cached[0] == path and cached[1] == mtime:
        return cached[2]
    text = path.read_text(encoding="utf-8")
    _template_cache[name] = (path, mtime, text)
    return text


def build_context() -> dict[str, str]:
    """Standard substitution variables, all sourced from config.yaml."""
    agent_cfg = get_agent_config()
    product_cfg = get_product_config()
    now_ct = datetime.now(ZoneInfo("America/Chicago"))
    spec_fields = ", ".join(
        f.get("label", f.get("key")) for f in product_cfg.get("spec_fields", [])
    )
    return {
        "business_name": business_name(),
        "business_address": business_address(),
        "business_phone": business_phone(),
        "business_hours": business_hours(),
        "product_singular": product_singular(),
        "product_plural": product_plural(),
        "product_plural_title": product_plural().title(),
        "personality": agent_cfg.get("personality", "friendly and professional"),
        "greeting": agent_cfg.get("greeting", f"Welcome to {business_name()}."),
        "spec_fields": spec_fields,
        "today_str": now_ct.strftime("%A, %B %d, %Y"),
        "today_iso": now_ct.strftime("%Y-%m-%d"),
    }


def render_prompt(name: str, **extra: str) -> str:
    """Render prompts/<name>.md with the standard context plus overrides."""
    template = Template(_load_template(name))
    context = {**build_context(), **extra}
    missing = set(template.get_identifiers()) - set(context)
    if missing:
        raise ValueError(
            f"Prompt '{name}' has unresolved variables: {sorted(missing)}. "
            "Add them to build_context() or pass them to render_prompt()."
        )
    return template.substitute(context)


def available_prompts() -> list[str]:
    return sorted(p.stem for p in _REPO_PROMPT_DIR.glob("*.md") if p.stem != "README")
