from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

FETCH_BATCH = 500
ACTION_BATCH = 200
GROUP_MIN_COUNT = 5
GROUP_MIN_SIZE = 20 * 1024 * 1024

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIRECTORY = ".data"
DATABASE_FILENAME = "catalog.sqlite"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    address: str
    app_password: str = field(repr=False)


def load_settings() -> Settings:
    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    app_password = "".join(os.environ.get("GMAIL_APP_PASSWORD", "").split())

    if not address:
        raise ConfigError(
            "Variável GMAIL_ADDRESS ausente: defina o endereço do Gmail no ambiente."
        )
    if not app_password:
        raise ConfigError(
            "Variável GMAIL_APP_PASSWORD ausente: defina a senha de app do Gmail no ambiente."
        )
    return Settings(address=address, app_password=app_password)


def database_path(root: Path | None = None) -> Path:
    base = PROJECT_ROOT if root is None else root
    directory = base / DATA_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    return directory / DATABASE_FILENAME
