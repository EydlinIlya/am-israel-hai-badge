from __future__ import annotations

import os
import tomllib
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _PROJECT_ROOT / "config.toml"


def _load_config() -> dict:
    with open(_CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def load_area_names() -> list[str]:
    """Load area names from BADGE_AREAS env var or config.toml."""
    env = os.environ.get("BADGE_AREAS", "").strip()
    if env:
        return [n.strip() for n in env.split(",") if n.strip()]
    return _load_config()["area"]["names"]


def load_github_username() -> str:
    """Load GitHub username from env vars or config.toml.

    Priority:
    1. GITHUB_USERNAME env var (explicit override, useful for local dev)
    2. GITHUB_REPOSITORY env var (set automatically in GitHub Actions as owner/repo)
    3. config.toml [github].username
    """
    env = os.environ.get("GITHUB_USERNAME", "").strip()
    if env:
        return env
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if repo and "/" in repo:
        return repo.split("/")[0]
    return _load_config().get("github", {}).get("username", "")
