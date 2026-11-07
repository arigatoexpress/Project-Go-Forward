"""The frontend image stage must see every repo-root JSON the SPA imports.

``listingRoutes.js`` imports ``data/*.json`` with a relative path that climbs
out of ``frontend/``. Vitest and ``npm run build`` run against a full
checkout, so those jobs resolve the file. The ``frontend-builder`` stage's
WORKDIR is ``/frontend`` and only receives what the Dockerfile copies before
``npm run build``. Cloud Build run 42393680533 failed on
``manufacturer_cdn_unreachable.json`` because only the extension-repairs
table was copied to ``/data``.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Load the classifier module directly. tools/__init__.py pulls in document
# and marketing deps this path check does not need.
_PC_PATH = ROOT / "tools" / "photo_classifier.py"
_spec = importlib.util.spec_from_file_location("photo_classifier_docker_imports", _PC_PATH)
assert _spec is not None and _spec.loader is not None
photo_classifier = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = photo_classifier
_spec.loader.exec_module(photo_classifier)
FRONTEND_SRC = ROOT / "frontend" / "src"
DOCKERFILE = ROOT / "Dockerfile"
IMPORT_SPEC = re.compile(
    r"""(?:import|export)\s+(?:[^'";\n]*?\s+from\s+)?"""
    r"""(?P<q>['"])(?P<spec>(?:\.\./)+data/[^'"]+\.json)(?P=q)"""
)
COPY_LINE = re.compile(r"^COPY(?:\s+--[^\s]+)*\s+(?P<srcs>.+?)\s+(?P<dest>\S+)\s*$")


def _frontend_builder_before_build(dockerfile: str) -> str:
    match = re.search(
        r"^FROM node:.*?AS frontend-builder\b.*?(?=^FROM |\Z)",
        dockerfile,
        re.M | re.S,
    )
    assert match, "frontend-builder stage missing"
    stage = match.group(0)
    before, sep, _after = stage.partition("RUN npm run build")
    assert sep, "frontend-builder never runs npm run build"
    return before


def _copy_instructions(stage_before_build: str) -> list[tuple[str, str]]:
    copies: list[tuple[str, str]] = []
    logical: list[str] = []
    buf = ""
    for raw in stage_before_build.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            if buf:
                logical.append(buf.strip())
                buf = ""
            continue
        if stripped.endswith("\\"):
            buf += stripped[:-1].strip() + " "
            continue
        buf += stripped
        logical.append(buf.strip())
        buf = ""
    if buf:
        logical.append(buf.strip())
    for line in logical:
        if not line.startswith("COPY "):
            continue
        match = COPY_LINE.match(line)
        assert match, line
        dest = match.group("dest")
        for src in match.group("srcs").split():
            copies.append((src, dest))
    return copies


def _repo_json_path(source_rel: str, spec: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_rel), spec))


def _image_json_path(source_rel: str, spec: str) -> str:
    """Where Vite resolves ``spec`` when the frontend tree is at ``/frontend``."""
    assert source_rel.startswith("frontend/")
    inside = "/" + source_rel[len("frontend/") :]
    return posixpath.normpath(posixpath.join(posixpath.dirname(inside), spec))


def _repo_data_imports() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for match in IMPORT_SPEC.finditer(text):
            spec = match.group("spec")
            found.append((rel, spec, _repo_json_path(rel, spec)))
    return found


def _placed_path(src: str, dest: str, repo_json: str) -> str | None:
    if any(ch in src for ch in "*?["):
        if not fnmatch.fnmatchcase(repo_json, src):
            return None
    elif src.endswith("/"):
        if not repo_json.startswith(src):
            return None
    elif src != repo_json:
        return None
    if dest.endswith("/"):
        return dest + posixpath.basename(repo_json)
    if any(ch in src for ch in "*?[") or src.endswith("/"):
        return None
    return dest


def _covered(copies: list[tuple[str, str]], repo_json: str, image_path: str) -> bool:
    return any(_placed_path(src, dest, repo_json) == image_path for src, dest in copies)


def test_frontend_image_build_can_resolve_repo_root_json_imports():
    imports = _repo_data_imports()
    names = {posixpath.basename(repo_json) for _rel, _spec, repo_json in imports}
    assert names == {
        "manufacturer_cdn_extension_repairs.json",
        "manufacturer_cdn_unreachable.json",
    }
    copies = _copy_instructions(
        _frontend_builder_before_build(DOCKERFILE.read_text(encoding="utf-8"))
    )
    missing = []
    for source_rel, spec, repo_json in imports:
        assert (ROOT / repo_json).is_file(), repo_json
        image_path = _image_json_path(source_rel, spec)
        assert image_path == f"/data/{posixpath.basename(repo_json)}"
        if not _covered(copies, repo_json, image_path):
            missing.append(f"{source_rel} imports {repo_json}, which is not at {image_path}")
    assert missing == []


def test_python_and_frontend_share_the_unreachable_cdn_key_file():
    js_imports = [
        item
        for item in _repo_data_imports()
        if item[2].endswith("manufacturer_cdn_unreachable.json")
        and item[0] == "frontend/src/utils/listingRoutes.js"
    ]
    assert len(js_imports) == 1
    repo_json = (ROOT / js_imports[0][2]).resolve()
    assert repo_json.is_file()
    assert photo_classifier._CDN_UNREACHABLE_PATH == repo_json
    keys = json.loads(repo_json.read_text(encoding="utf-8"))
    assert isinstance(keys, list)
    assert keys
    assert photo_classifier._UNREACHABLE_CDN_KEYS == frozenset(str(key) for key in keys if key)
    assert len(keys) == len(photo_classifier._UNREACHABLE_CDN_KEYS)


def test_copying_only_the_extension_repairs_table_leaves_unreachable_keys_unresolved():
    """The Dockerfile shape that failed Cloud Build must stay a failing case."""
    stage = (
        "FROM node:20-slim AS frontend-builder\n"
        "COPY data/manufacturer_cdn_extension_repairs.json "
        "/data/manufacturer_cdn_extension_repairs.json\n"
        "RUN npm run build\n"
    )
    copies = _copy_instructions(stage)
    repo_json = "data/manufacturer_cdn_unreachable.json"
    image_path = "/data/manufacturer_cdn_unreachable.json"
    assert not _covered(copies, repo_json, image_path)
    repairs = "data/manufacturer_cdn_extension_repairs.json"
    assert _covered(copies, repairs, "/data/manufacturer_cdn_extension_repairs.json")
