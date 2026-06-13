"""Agnosticism grep-gates, enforced as tests so CI keeps them true.

Contracts (from the fleet handoff, applied to this repo's safe subset):
- Hardware/OS: no machine-specific absolute paths, no device assumptions,
  no local-LLM endpoints baked into source.
- Model: model identifiers appear only in config (config.yaml /
  config_loader defaults) or as documented env-var fallbacks — never inline
  at call sites.

The allowlist is exact: adding a new hardcoded model string anywhere else
fails this suite with a pointer to the convention.
"""

import re
from pathlib import Path

REPO = Path(__file__).parent.parent

SOURCE_GLOBS = ("*.py", "*.js", "*.jsx", "*.ts", "*.tsx")
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    ".claude",
    "data",
    "docs",
    "tests",
    "scripts",
    "scratch",  # local-only scratch files (untracked, never shipped)
}

# Hardware/OS/local-host assumptions that must never appear in source.
FORBIDDEN_PATTERNS = {
    "machine-specific home path": re.compile(r"/Users/[A-Za-z]"),
    "windows drive path": re.compile(r"[\"']C:\\\\"),
    "local ollama endpoint": re.compile(r"localhost:11434|127\.0\.0\.1:11434"),
    "GPU device assumption": re.compile(r"cuda:\d|RTX ?\d{4}|raspberry ?pi|Mac mini", re.I),
}

# Model identifiers may appear ONLY in these files, and only as config/env
# defaults (config.yaml is the operative value everywhere).
MODEL_PATTERN = re.compile(r"[\"'](gemini|claude|gpt)-[0-9a-z]", re.I)
MODEL_ALLOWLIST = {
    "config_loader.py",  # config default fallback
    "tools/marketing_tools.py",  # THO_GEMINI_MODEL / fallback env defaults
    "tools/form_extraction.py",  # THO_EXTRACTION_MODEL env default
    "tools/lead_name_backfill.py",  # THO_BACKFILL_MODEL env default
}


def _source_files():
    for glob in SOURCE_GLOBS:
        for path in REPO.rglob(glob):
            if EXCLUDED_PARTS & set(path.parts):
                continue
            yield path


def test_no_hardware_or_machine_assumptions():
    violations = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(text):
                violations.append(f"{path.relative_to(REPO)}: {label}")
    assert not violations, (
        "Hardware/OS agnosticism violations (use config/env instead):\n" + "\n".join(violations)
    )


def test_model_names_only_in_config_or_env_defaults():
    violations = []
    for path in _source_files():
        rel = str(path.relative_to(REPO))
        if rel in MODEL_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            if MODEL_PATTERN.search(line):
                violations.append(f"{rel}:{lineno}: {line.strip()[:80]}")
    assert not violations, (
        "Hardcoded model identifiers outside config/env defaults. Route them "
        "through config.yaml (config_loader.model_name()) or an env-var "
        "default, then extend MODEL_ALLOWLIST deliberately:\n" + "\n".join(violations)
    )


def test_allowlisted_files_use_env_or_config_defaults():
    """The allowlist entries must keep the env/config-default shape."""
    for rel in MODEL_ALLOWLIST - {"config_loader.py"}:
        text = (REPO / rel).read_text(encoding="utf-8")
        for line in text.splitlines():
            if MODEL_PATTERN.search(line):
                assert (
                    "environ" in line or "env" in line.lower()
                ), f"{rel}: model literal not wrapped in an env default: {line.strip()}"
