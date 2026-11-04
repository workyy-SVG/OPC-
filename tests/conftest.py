import pytest


@pytest.fixture(autouse=True)
def isolated_ai_quota(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_QUOTA_DB', str(tmp_path / 'ai_usage.sqlite3'))
