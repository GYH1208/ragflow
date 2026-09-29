#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import pytest

from test.testcases.restful_api.helpers.client import RestClient


@pytest.mark.p1
def test_system_ping(rest_client):
    res = rest_client.get("/system/ping")
    assert res.status_code == 200
    assert res.text == "pong"


@pytest.mark.p1
def test_system_version(rest_client):
    res = rest_client.get("/system/version")
    assert res.status_code == 200
    payload = res.json()
    assert payload["code"] == 0, payload
    assert payload["data"], payload


@pytest.mark.p2
def test_system_status_requires_auth(rest_client_noauth):
    res = rest_client_noauth.get("/system/status")
    assert res.status_code == 401
    payload = res.json()
    assert payload["code"] == 401, payload
    assert "Unauthorized" in payload["message"], payload


@pytest.mark.p2
def test_system_status_contract(rest_client):
    res = rest_client.get("/system/status")
    assert res.status_code == 200
    payload = res.json()
    assert payload["code"] == 0, payload
    for key in ("doc_engine", "storage", "database", "redis"):
        assert key in payload["data"], payload


@pytest.mark.p2
def test_system_config_no_auth_required(rest_client_noauth):
    res = rest_client_noauth.get("/system/config")
    assert res.status_code == 200
    payload = res.json()
    assert payload["code"] == 0, payload
    assert "registerEnabled" in payload["data"], payload
    assert "disablePasswordLogin" in payload["data"], payload


@pytest.mark.p2
def test_system_healthz_contract(rest_client_noauth):
    res = rest_client_noauth.get("/system/healthz")
    assert res.status_code in (200, 500)
    payload = res.json()
    assert isinstance(payload, dict), payload
    assert payload, payload


@pytest.mark.p2
def test_system_tokens_auth_and_crud(rest_client, rest_client_noauth, create_dataset):
    unauth_list = rest_client_noauth.get("/system/tokens")
    assert unauth_list.status_code == 401
    unauth_list_payload = unauth_list.json()
    assert unauth_list_payload["code"] == 401, unauth_list_payload

    full_create_res = rest_client.post(
        "/system/tokens",
        json={"name": "REST test full-access key", "key_type": "full_access"},
    )
    assert full_create_res.status_code == 200
    full_create_payload = full_create_res.json()
    assert full_create_payload["code"] == 0, full_create_payload
    full_token = full_create_payload["data"]["token"]
    assert full_create_payload["data"]["key_type"] == "full_access"
    assert full_create_payload["data"]["legacy"] is False

    dataset_id = create_dataset("system_token_scope")
    scoped_create_res = rest_client.post(
        "/system/tokens",
        json={
            "name": "REST test retrieval key",
            "key_type": "retrieval",
            "allowed_dataset_ids": [dataset_id],
            "expires_in_days": 90,
        },
    )
    assert scoped_create_res.status_code == 200
    scoped_create_payload = scoped_create_res.json()
    assert scoped_create_payload["code"] == 0, scoped_create_payload
    scoped_token = scoped_create_payload["data"]["token"]
    assert scoped_token.startswith("ragflow-rk-")
    assert "beta" not in scoped_create_payload["data"]

    list_res = rest_client.get("/system/tokens")
    assert list_res.status_code == 200
    list_payload = list_res.json()
    assert list_payload["code"] == 0, list_payload
    assert isinstance(list_payload["data"], list), list_payload
    by_token = {item["token"]: item for item in list_payload["data"]}
    assert by_token[full_token]["allowed_dataset_ids"] == []
    assert by_token[full_token]["expires_at"] is None
    assert by_token[scoped_token]["allowed_dataset_ids"] == [dataset_id]

    patch_res = rest_client.patch(
        "/system/tokens",
        json={"token": scoped_token, "name": "REST test retrieval key updated"},
    )
    assert patch_res.status_code == 200
    patch_payload = patch_res.json()
    assert patch_payload["code"] == 0, patch_payload
    assert patch_payload["data"]["name"] == "REST test retrieval key updated"
    assert patch_payload["data"]["allowed_dataset_ids"] == [dataset_id]

    retrieval_client = RestClient(token=scoped_token)
    for method, path, body in (
        ("GET", "/system/tokens", None),
        ("POST", "/system/tokens", {"name": "denied", "key_type": "full_access"}),
        ("PATCH", "/system/tokens", {"token": scoped_token, "enabled": True}),
        ("DELETE", "/system/tokens", {"token": scoped_token}),
    ):
        denied_res = retrieval_client.request(method, path, json=body)
        assert denied_res.status_code == 403
        assert denied_res.json()["code"] == 403

    disable_res = rest_client.patch("/system/tokens", json={"token": scoped_token, "enabled": False})
    assert disable_res.status_code == 200
    disable_payload = disable_res.json()
    assert disable_payload["code"] == 0, disable_payload
    assert disable_payload["data"]["enabled"] is False

    for token in (scoped_token, full_token):
        delete_res = rest_client.delete("/system/tokens", json={"token": token})
        assert delete_res.status_code == 200
        delete_payload = delete_res.json()
        assert delete_payload["code"] == 0, delete_payload
        assert delete_payload["data"] is True, delete_payload

    delete_missing = rest_client.delete("/system/tokens", json={"token": "missing_token"})
    assert delete_missing.status_code == 200
    delete_missing_payload = delete_missing.json()
    assert delete_missing_payload["code"] == 0, delete_missing_payload
    assert delete_missing_payload["data"] is True, delete_missing_payload


@pytest.mark.p2
def test_system_stats_auth_and_shape(rest_client, rest_client_noauth):
    unauth_res = rest_client_noauth.get("/system/stats")
    assert unauth_res.status_code == 401
    unauth_payload = unauth_res.json()
    assert unauth_payload["code"] == 401, unauth_payload

    res = rest_client.get("/system/stats")
    assert res.status_code == 200
    payload = res.json()
    assert payload["code"] == 0, payload
    data = payload["data"]
    for key in ("pv", "uv", "speed", "tokens", "round", "thumb_up"):
        assert key in data, payload
        assert isinstance(data[key], list), payload


@pytest.mark.p2
def test_system_oceanbase_status_auth_contract(rest_client, rest_client_noauth):
    unauth = rest_client_noauth.get("/system/oceanbase/status")
    assert unauth.status_code == 401
    assert unauth.json()["code"] == 401

    res = rest_client.get("/system/oceanbase/status")
    assert res.status_code == 200
    payload = res.json()
    assert payload["code"] in (0, 500), payload
    assert "data" in payload, payload


@pytest.mark.p2
def test_system_log_config_routes_auth_and_validation(rest_client, rest_client_noauth):
    unauth = rest_client_noauth.get("/system/config/log")
    assert unauth.status_code == 401
    assert unauth.json()["code"] == 401

    levels = rest_client.get("/system/config/log")
    assert levels.status_code == 200
    levels_payload = levels.json()
    assert levels_payload["code"] == 0, levels_payload
    assert isinstance(levels_payload["data"], dict), levels_payload

    missing_body = rest_client.put("/system/config/log", json={})
    assert missing_body.status_code == 200
    missing_payload = missing_body.json()
    assert missing_payload["code"] == 102, missing_payload
    assert "pkg_name and level are required" in missing_payload["message"], missing_payload

    invalid_level = rest_client.put("/system/config/log", json={"pkg_name": "rag", "level": "NOT_A_LEVEL"})
    assert invalid_level.status_code == 200
    invalid_payload = invalid_level.json()
    assert invalid_payload["code"] == 102, invalid_payload
    assert "Invalid log level" in invalid_payload["message"], invalid_payload
