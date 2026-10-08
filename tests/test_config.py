import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.mark.parametrize("value", ["", "bad", "sqlite:///db", "postgresql+psycopg://localhost"])
def test_invalid_database_config(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=SecretStr(value), _env_file=None)


def test_missing_config_clear(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        get_settings()


def test_config_error_hides_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "bad://user:secret-password@localhost/db")
    with pytest.raises(RuntimeError) as error:
        get_settings()
    assert "secret-password" not in str(error.value)


async def test_startup_requires_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    app = create_app()
    with pytest.raises(RuntimeError, match="DATABASE_URL is required"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must reject missing configuration")


async def test_startup_unavailable_database(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(
        Settings(
            database_url=SecretStr(
                "postgresql+psycopg://user:secret-password@127.0.0.1:1/db?connect_timeout=1"
            )
        )
    )
    with pytest.raises(RuntimeError, match="Database unavailable") as error:
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must reject an unavailable database")
    assert "secret-password" not in str(error.value)
    assert "secret-password" not in caplog.text
