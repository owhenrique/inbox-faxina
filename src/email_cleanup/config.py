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


def parse_env_file(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def read_env_file(path: Path) -> dict[str, str]:
    try:
        return parse_env_file(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def load_settings(env_file: Path | None = None) -> Settings:
    path = PROJECT_ROOT / ".env" if env_file is None else env_file
    values = {**read_env_file(path), **os.environ}
    address = values.get("GMAIL_ADDRESS", "").strip()
    app_password = "".join(values.get("GMAIL_APP_PASSWORD", "").split())

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
