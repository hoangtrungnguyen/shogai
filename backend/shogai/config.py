"""Runtime settings for Shōgai, read from backend/.env and the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

# backend/.env is the source of truth for this app. override=True so that a
# GEMINI_API_KEY exported globally in the user's shell (possibly stale or for a
# different project) does not silently shadow the key configured for Shōgai.
load_dotenv(BACKEND_DIR / ".env", override=True)


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _resolve_dir(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


def _read_version() -> str:
    try:
        return (PROJECT_DIR / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


# Release version: the VERSION file at the repo root (copied into the Docker image).
# Docker builds also record the git commit and build time (see scripts/docker.sh).
VERSION = _read_version()
BUILD_COMMIT = os.environ.get("SHOGAI_COMMIT", "").strip() or None
BUILD_DATE = os.environ.get("SHOGAI_BUILD_DATE", "").strip() or None


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gemini_api_version: str
    model_analyze: str
    model_synthesize: str
    model_advisor: str
    analyze_thinking: str
    synthesize_thinking: str
    advisor_thinking: str
    data_dir: Path
    keep_media: bool
    language: str
    frontend_dist: Path
    host: str
    port: int
    shutdown_grace: float  # seconds running jobs get to finish when the server stops

    def with_overrides(self, **kwargs) -> "Settings":
        return replace(self, **kwargs)


def load_settings() -> Settings:
    key = (
        os.environ.get("SHOGAI_GEMINI_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or ""
    ).strip()
    return Settings(
        gemini_api_key=key or None,
        # The Gemini Developer API keys of the newer "AQ." format are accepted on
        # v1beta; google-genai >= 3 defaults to v1, so pin it explicitly.
        gemini_api_version=_env("SHOGAI_GEMINI_API_VERSION", "v1beta"),
        model_analyze=_env("SHOGAI_MODEL_ANALYZE", "gemini-flash-latest"),
        model_synthesize=_env("SHOGAI_MODEL_SYNTHESIZE", "gemini-flash-latest"),
        model_advisor=_env("SHOGAI_MODEL_ADVISOR", "gemini-flash-latest"),
        analyze_thinking=_env("SHOGAI_ANALYZE_THINKING", "low"),
        synthesize_thinking=_env("SHOGAI_SYNTHESIZE_THINKING", "medium"),
        advisor_thinking=_env("SHOGAI_ADVISOR_THINKING", "medium"),
        data_dir=_resolve_dir(_env("SHOGAI_DATA_DIR", "data")),
        keep_media=_env_bool("SHOGAI_KEEP_MEDIA", True),
        language=_env("SHOGAI_LANGUAGE", "English"),
        frontend_dist=_resolve_dir(_env("SHOGAI_FRONTEND_DIST", "frontend/dist")),
        host=_env("SHOGAI_HOST", "127.0.0.1"),
        port=int(_env("SHOGAI_PORT", "8000")),
        shutdown_grace=float(_env("SHOGAI_SHUTDOWN_GRACE", "240")),
    )
