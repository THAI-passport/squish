"""Tests for backend/env_manager.py (server-side .env SMTP profile storage)."""

import env_manager


def test_add_list_update_delete_wipe_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(env_manager, "ENV_PATH", tmp_path / ".env")

    idx = env_manager.add_profile({
        "name": "Work O365", "server": "smtp.office365.com", "port": 587,
        "username": "sender@company.com", "password": "hunter2", "from_name": "Finance",
    })
    assert idx == 1

    profiles = env_manager.list_profiles_masked()
    assert len(profiles) == 1
    assert profiles[0]["password"] == "•" * 8  # masked, never the real value
    assert profiles[0]["server"] == "smtp.office365.com"

    raw = env_manager.get_profile_for_dispatch(1)
    assert raw["password"] == "hunter2"  # server-side dispatch path only

    env_manager.update_profile_meta(1, name="Renamed", from_name="New Name")
    profiles = env_manager.list_profiles_masked()
    assert profiles[0]["name"] == "Renamed"
    assert profiles[0]["from_name"] == "New Name"
    # Password unaffected by a metadata-only update.
    assert env_manager.get_profile_for_dispatch(1)["password"] == "hunter2"

    env_manager.delete_profile(1)
    assert env_manager.list_profiles_masked() == []


def test_max_five_profiles_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(env_manager, "ENV_PATH", tmp_path / ".env")
    for i in range(5):
        env_manager.add_profile({
            "server": "smtp.example.com", "port": 587,
            "username": f"user{i}@example.com", "password": "pw",
        })
    try:
        env_manager.add_profile({
            "server": "smtp.example.com", "port": 587,
            "username": "user6@example.com", "password": "pw",
        })
        assert False, "expected ValueError for 6th profile"
    except ValueError as exc:
        assert "Maximum" in str(exc)


def test_wipe_all_clears_only_smtp_keys(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("SOME_OTHER_VAR=keep-me\n", encoding="utf-8")
    monkeypatch.setattr(env_manager, "ENV_PATH", env_path)

    env_manager.add_profile({
        "server": "smtp.example.com", "port": 587,
        "username": "user@example.com", "password": "pw",
    })
    assert len(env_manager.list_profiles_masked()) == 1

    env_manager.wipe_all()
    assert env_manager.list_profiles_masked() == []
    assert "SOME_OTHER_VAR=keep-me" in env_path.read_text(encoding="utf-8")
