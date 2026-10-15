from __future__ import annotations

from pathlib import Path

import pytest

from email_cleanup import config as config_module
from email_cleanup.config import (
    ACTION_BATCH,
    FETCH_BATCH,
    GROUP_MIN_COUNT,
    GROUP_MIN_SIZE,
    ConfigError,
    Settings,
    database_path,
    load_settings,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    return monkeypatch


def test_load_settings_reads_environment(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GMAIL_ADDRESS", "user@gmail.com")
    clean_env.setenv("GMAIL_APP_PASSWORD", "abcdefghijklmnop")

    settings = load_settings()

    assert isinstance(settings, Settings)
    assert settings.address == "user@gmail.com"
    assert settings.app_password == "abcdefghijklmnop"


def test_load_settings_normalizes_credentials(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GMAIL_ADDRESS", "  user@gmail.com  ")
    clean_env.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")

    settings = load_settings()

    assert settings.address == "user@gmail.com"
    assert settings.app_password == "abcdefghijklmnop"


def test_load_settings_missing_address(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GMAIL_APP_PASSWORD", "abcdefghijklmnop")

    with pytest.raises(ConfigError) as error:
        load_settings()

    assert "GMAIL_ADDRESS" in str(error.value)


def test_load_settings_missing_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GMAIL_ADDRESS", "user@gmail.com")

    with pytest.raises(ConfigError) as error:
        load_settings()

    assert "GMAIL_APP_PASSWORD" in str(error.value)


def test_load_settings_empty_address(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GMAIL_ADDRESS", "   ")
    clean_env.setenv("GMAIL_APP_PASSWORD", "abcdefghijklmnop")

    with pytest.raises(ConfigError):
        load_settings()


def test_load_settings_empty_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GMAIL_ADDRESS", "user@gmail.com")
    clean_env.setenv("GMAIL_APP_PASSWORD", "   ")

    with pytest.raises(ConfigError):
        load_settings()


def test_load_settings_error_does_not_leak_password(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("GMAIL_APP_PASSWORD", "supersecretapppassword")

    with pytest.raises(ConfigError) as error:
        load_settings()

    assert "supersecretapppassword" not in str(error.value)


def test_load_settings_reads_env_file(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "# comment\nexport GMAIL_ADDRESS='file@gmail.com'\n"
        'GMAIL_APP_PASSWORD="abcd efgh ijkl mnop"\n\nINVALID\n',
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.address == "file@gmail.com"
    assert settings.app_password == "abcdefghijklmnop"


def test_environment_takes_precedence_over_env_file(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "GMAIL_ADDRESS=file@gmail.com\nGMAIL_APP_PASSWORD=filepassword\n",
        encoding="utf-8",
    )
    clean_env.setenv("GMAIL_ADDRESS", "env@gmail.com")

    settings = load_settings()

    assert settings.address == "env@gmail.com"
    assert settings.app_password == "filepassword"


def test_missing_env_file_is_ignored(clean_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError):
        load_settings()


def test_database_path_uses_project_root_by_default() -> None:
    assert database_path() == PROJECT_ROOT / ".data" / "catalog.sqlite"


def test_database_path_creates_data_directory(tmp_path: Path) -> None:
    path = database_path(tmp_path)

    assert path == tmp_path / ".data" / "catalog.sqlite"
    assert path.parent.is_dir()
    assert not path.exists()

    assert database_path(tmp_path) == path


def test_constants() -> None:
    assert FETCH_BATCH == 500
    assert ACTION_BATCH == 200
    assert GROUP_MIN_COUNT == 5
    assert GROUP_MIN_SIZE == 20 * 1024 * 1024
