"""Paths and settings. Loads code/.env first; variables already set take priority."""
import os
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = CODE_DIR.parent


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip())


_load_env_file(CODE_DIR / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR", REPO_DIR))
DB_PATH = DATA_DIR / "data" / "ap.sqlite"
DOCS_DIR = DATA_DIR / "docs"
SCHEMA_PATH = DATA_DIR / "schema" / "result.schema.json"

MOCK_DIR = CODE_DIR / "mock_system"
TRACES_DIR = CODE_DIR / "traces"
RUNS_DIR = CODE_DIR / "runs"

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_TOOL_ROUNDS = 12
MAX_REVIEW_ROUNDS = 1
APPROVAL_LIMIT = 500_000  # AP-POL 8.2: payments above this need the AP manager
