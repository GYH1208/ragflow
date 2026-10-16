import pytest


@pytest.mark.parametrize("value", [-1, 86401, True, 1.5, "86400"])
@pytest.mark.parametrize("field", ["token_cache_ttl_seconds", "profile_cache_ttl_seconds"])
def test_invalid_cache_durations_are_rejected(value, field):
    from api.auth.workbuddy.config import WorkBuddyConfig

    with pytest.raises(ValueError, match="cache"):
        WorkBuddyConfig(**{field: value})


def test_cache_duration_env_and_zero_disable(monkeypatch):
    from api.auth.workbuddy.config import WorkBuddyConfig

    for key, value in {"ENABLED": "true", "ISSUER": "https://oauth2.account.tencent.com/authz/oidc/v2/123", "TENANT_ID": "123", "APPLICATION_ID": "app", "CLIENT_SECRET": "test-secret"}.items():
        monkeypatch.setenv("RAGFLOW_WORKBUDDY_" + key, value)
    assert WorkBuddyConfig.from_env().profile_cache_ttl_seconds == 86400
    monkeypatch.setenv("RAGFLOW_WORKBUDDY_TOKEN_CACHE_TTL_SECONDS", "0")
    monkeypatch.setenv("RAGFLOW_WORKBUDDY_PROFILE_CACHE_TTL_SECONDS", "600")
    conf = WorkBuddyConfig.from_env()
    assert conf.token_cache_ttl_seconds == 0
    assert conf.profile_cache_ttl_seconds == 600


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
