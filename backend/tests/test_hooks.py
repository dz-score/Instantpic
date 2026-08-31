"""The hook that enforces Rule 25 is itself enforced here.

Git hooks live in .git/hooks, which is not version controlled, so a tracked hook
only runs once someone points core.hooksPath at it. That "once" is exactly the
kind of step that does not happen — the same failure the hook exists to fix, one
level up. The test suite runs constantly, so it is what notices.
"""
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOKS_DIR = ".githooks"


def _git(*args):
    """Run a git command in the repo, or None if git/the repo is unavailable."""
    try:
        r = subprocess.run(["git", "-C", REPO, *args],
                           capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _is_repo():
    return _git("rev-parse", "--is-inside-work-tree") == "true"


def test_the_rule_25_hook_ships():
    """A hook nobody can install is not a mechanism."""
    hook = os.path.join(REPO, HOOKS_DIR, "pre-commit")
    assert os.path.exists(hook), f"{HOOKS_DIR}/pre-commit is missing"
    body = open(hook, encoding="utf-8").read()
    assert "check_duplication.py" in body
    # Width 4, not the default: at 7 it passed a branch that was duplicating.
    assert "--width 4" in body


def test_hooks_path_is_configured():
    """One-time per clone: git config core.hooksPath .githooks

    Skipped rather than failed where there is no git repo to configure — a
    source tarball or a CI checkout without history should not fail the suite
    over a developer setting.
    """
    if not _is_repo():
        pytest.skip("not a git work tree")

    configured = _git("config", "core.hooksPath")
    assert configured == HOOKS_DIR, (
        f"core.hooksPath is {configured!r}, expected {HOOKS_DIR!r}. "
        f"The Rule 25 pre-commit hook is not installed and will not run. "
        f"Fix with: git config core.hooksPath {HOOKS_DIR}"
    )
