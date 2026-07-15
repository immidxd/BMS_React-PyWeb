from pathlib import Path

from backend.services import runtime_config


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def test_credentials_file_prefers_existing_env(monkeypatch, tmp_path):
    env_file = _touch(tmp_path / "env" / "service.json")
    monkeypatch.setenv("TEST_SHEETS_CREDS", str(env_file))
    monkeypatch.setattr(runtime_config, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(runtime_config, "_repo_root", lambda: tmp_path / "repo")

    assert runtime_config.credentials_file("TEST_SHEETS_CREDS") == str(env_file.resolve())


def test_credentials_file_ignores_stale_env_and_uses_repo_fallback(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    fallback = _touch(repo / "mcp-google-sheets" / "working_credentials.json")
    monkeypatch.setenv("TEST_SHEETS_CREDS", str(tmp_path / "deleted-project" / "service.json"))
    monkeypatch.setattr(runtime_config, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(runtime_config, "_repo_root", lambda: repo)

    assert runtime_config.credentials_file("TEST_SHEETS_CREDS") == str(fallback.resolve())


def test_credentials_file_prefers_bms_data_dir_over_repo(monkeypatch, tmp_path):
    config = tmp_path / "bms-data"
    config_file = _touch(config / "working_credentials.json")
    repo_file = _touch(tmp_path / "repo" / "mcp-google-sheets" / "working_credentials.json")
    monkeypatch.delenv("TEST_SHEETS_CREDS", raising=False)
    monkeypatch.setattr(runtime_config, "config_dir", lambda: config)
    monkeypatch.setattr(runtime_config, "_repo_root", lambda: tmp_path / "repo")

    assert runtime_config.credentials_file("TEST_SHEETS_CREDS") == str(config_file.resolve())
    assert runtime_config.credentials_file("TEST_SHEETS_CREDS") != str(repo_file.resolve())


def test_credentials_file_returns_none_when_no_candidate_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("TEST_SHEETS_CREDS", raising=False)
    monkeypatch.setattr(runtime_config, "config_dir", lambda: tmp_path / "config")
    monkeypatch.setattr(runtime_config, "_repo_root", lambda: tmp_path / "repo")

    assert runtime_config.credentials_file("TEST_SHEETS_CREDS") is None


def test_all_ui_sheets_modes_have_an_explicit_safe_route():
    from backend.utils.parsing_modes import SHEETS_MODE_ROUTES, get_parsing_modes

    visible_modes = {item["id"] for item in get_parsing_modes()}
    assert visible_modes == set(SHEETS_MODE_ROUTES)
    assert SHEETS_MODE_ROUTES == {
        "sheets_products_quick": ("products", "quick"),
        "sheets_products_full": ("products", "full"),
        "sheets_orders_quick": ("orders", "quick"),
        "sheets_orders_full": ("orders", "full"),
        "sheets_full_quick": ("full", "quick"),
        "sheets_full_full": ("full", "full"),
        "sheets_workspace": ("workspace", "quick"),
    }
