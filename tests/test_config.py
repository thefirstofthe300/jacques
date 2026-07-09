import pathlib

import pytest

from jacques.config import Settings


def _write_toml(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _toml_path(home: pathlib.Path) -> pathlib.Path:
    return home / ".config" / "jacques" / "config.toml"


def test_settings_loads_quality_from_toml(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _write_toml(_toml_path(home), "handbrake_quality = 28\n")

    monkeypatch.setattr("jacques.config.Path.home", lambda: home)
    monkeypatch.delenv("JACQUES_HANDBRAKE_QUALITY", raising=False)

    s = Settings()
    assert s.handbrake_quality == 28


def test_settings_missing_toml_uses_defaults(monkeypatch, tmp_path):
    home = tmp_path / "home"
    # No TOML file written — directory doesn't even exist.

    monkeypatch.setattr("jacques.config.Path.home", lambda: home)
    monkeypatch.delenv("JACQUES_HANDBRAKE_QUALITY", raising=False)

    s = Settings()
    assert s.handbrake_quality == 20


def test_settings_loads_preset_from_toml(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _write_toml(_toml_path(home), 'handbrake_preset = "slow"\n')

    monkeypatch.setattr("jacques.config.Path.home", lambda: home)
    monkeypatch.delenv("JACQUES_HANDBRAKE_PRESET", raising=False)

    s = Settings()
    assert s.handbrake_preset == "slow"


def test_env_var_overrides_toml(monkeypatch, tmp_path):
    home = tmp_path / "home"
    _write_toml(_toml_path(home), "handbrake_quality = 28\n")

    monkeypatch.setattr("jacques.config.Path.home", lambda: home)
    monkeypatch.setenv("JACQUES_HANDBRAKE_QUALITY", "15")

    s = Settings()
    assert s.handbrake_quality == 15
