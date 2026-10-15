import pytest


def test_disabled_config_needs_no_credentials(monkeypatch):
    from api.auth.workbuddy.config import WorkBuddyConfig

    monkeypatch.delenv("RAGFLOW_WORKBUDDY_ENABLED", raising=False)
    assert WorkBuddyConfig.from_env().enabled is False


def test_enabled_config_requires_pinned_enterprise_and_hides_secret(monkeypatch):
    from api.auth.workbuddy.config import WorkBuddyConfig

    monkeypatch.setenv("RAGFLOW_WORKBUDDY_ENABLED", "true")
    with pytest.raises(ValueError):
        WorkBuddyConfig.from_env()
    for key, value in {
        "ISSUER": "https://oauth2.account.tencent.com/authz/oidc/v2/123",
        "TENANT_ID": "123",
        "APPLICATION_ID": "i-test",
        "CLIENT_SECRET": "private-secret",
        "REQUESTING_CLIENTS": "codebuddy",
    }.items():
        monkeypatch.setenv("RAGFLOW_WORKBUDDY_" + key, value)
    assert "private-secret" not in repr(WorkBuddyConfig.from_env())
    monkeypatch.setenv("RAGFLOW_WORKBUDDY_TENANT_ID", "456")
    with pytest.raises(ValueError):
        WorkBuddyConfig.from_env()
