"""Central configuration loaded from .env. Import get_settings() everywhere else."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

_REQUIRED_FOR_LLM = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")


@dataclass(frozen=True)
class Settings:
    llm_base_url: str
    llm_api_key: str
    llm_model: str

    embedding_provider: str
    embedding_model: str
    local_embedding_model: str

    telegram_bot_token: str

    products_csv_path: Path
    chroma_dir: Path
    sqlite_path: Path
    bot_threads_sqlite_path: Path

    nudge_delay_seconds: int
    reoffer_delay_seconds: int


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else PROJECT_ROOT / path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    missing = [name for name in _REQUIRED_FOR_LLM if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            f"{', '.join(missing)}. Copy .env.example to .env and fill them in."
        )

    return Settings(
        llm_base_url=os.environ["LLM_BASE_URL"],
        llm_api_key=os.environ["LLM_API_KEY"],
        llm_model=os.environ["LLM_MODEL"],
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "proxy"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        local_embedding_model=os.getenv(
            "LOCAL_EMBEDDING_MODEL",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        ),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        products_csv_path=_resolve(os.getenv("PRODUCTS_CSV_PATH", "data/products_500.csv")),
        chroma_dir=_resolve(os.getenv("CHROMA_DIR", "chroma_db")),
        sqlite_path=_resolve(os.getenv("SQLITE_PATH", "checkpoints.sqlite")),
        bot_threads_sqlite_path=_resolve(
            os.getenv("BOT_THREADS_SQLITE_PATH", "bot_threads.sqlite")
        ),
        nudge_delay_seconds=int(os.getenv("NUDGE_DELAY_SECONDS", "3600")),
        reoffer_delay_seconds=int(os.getenv("REOFFER_DELAY_SECONDS", "172800")),
    )
