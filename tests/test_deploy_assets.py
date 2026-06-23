"""Guards that runtime assets survive the deploy filter.

Regression test for the 2026-06-23 chat outage: `.dockerignore` / `.gcloudignore`
both had a blanket `*.md` rule (to keep docs out of the image) that ALSO stripped
the agent prompt templates `prompts/*.md`. Those are RUNTIME assets — `render_prompt()`
reads them at startup — so the deployed container was missing `prompts/sales_agent.md`,
the ADK runner failed to init, and every chat returned "AI service temporarily
unavailable". The files exist in git and locally, so nothing else caught it.

These tests assert the deploy ignore-files do NOT exclude the runtime prompts.
`gcloud run deploy --source .` applies BOTH files (gcloudignore at upload,
dockerignore at build), so both must be checked.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from prompt_loader import available_prompts  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
IGNORE_FILES = [".dockerignore", ".gcloudignore"]


def _runtime_prompt_paths() -> list[str]:
    """Repo-relative POSIX paths of every prompt template the agents load at runtime."""
    paths = [f"prompts/{name}.md" for name in available_prompts()]
    assert paths, "no prompt templates discovered — prompt_loader changed?"
    # The three that the ADK runner needs to start (see root_agent.py).
    for required in ("prompts/sales_agent.md", "prompts/root_agent.md", "prompts/service_agent.md"):
        assert required in paths, f"{required} missing from prompts/"
    return paths


def _is_ignored(ignore_text: str, rel_path: str, tmp_path: Path) -> bool:
    """True if `rel_path` would be excluded by ignore_text (gitignore/gcloudignore semantics).

    Uses `git check-ignore`, the reference implementation of the pattern syntax that
    `.gcloudignore` shares with `.gitignore` and that `.dockerignore` matches for the
    simple patterns these files use (including `!` negations and their ordering).
    """
    work = tmp_path / rel_path.replace("/", "_")
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    (work / ".gitignore").write_text(ignore_text, encoding="utf-8")
    target = work / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()
    # check-ignore exits 0 when the path IS ignored, 1 when it is not.
    result = subprocess.run(
        ["git", "check-ignore", "-q", rel_path], cwd=work
    )
    return result.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git required to evaluate ignore patterns")
@pytest.mark.parametrize("ignore_file", IGNORE_FILES)
def test_runtime_prompts_not_stripped_by_deploy_ignore(ignore_file, tmp_path):
    ignore_path = REPO_ROOT / ignore_file
    assert ignore_path.is_file(), f"{ignore_file} missing"
    ignore_text = ignore_path.read_text(encoding="utf-8")

    excluded = [
        p for p in _runtime_prompt_paths() if _is_ignored(ignore_text, p, tmp_path)
    ]
    assert not excluded, (
        f"{ignore_file} would strip runtime prompt(s) from the deploy image: {excluded}. "
        f"The blanket `*.md` rule must be followed by `!prompts/*.md` (see the chat-outage "
        f"regression this test guards)."
    )


def test_runtime_prompt_files_exist_in_repo():
    """The prompts must actually be present and committed, not just un-ignored."""
    missing = [p for p in _runtime_prompt_paths() if not (REPO_ROOT / p).is_file()]
    assert not missing, f"runtime prompt files absent from repo: {missing}"
