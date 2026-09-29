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

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

from api.db.db_models import ScopedAPIToken
from api.db.services.api_service import ScopedAPITokenService
from test.testcases.restful_api.helpers.client import RestClient
from test.testcases.utils import wait_for


def _expect_json(response, *, status: int, code: int, label: str) -> dict:
    """Validate public responses without putting API-key material in failures."""
    if response.status_code != status:
        pytest.fail(f"{label}: expected HTTP {status}, got {response.status_code}", pytrace=False)
    try:
        payload = response.json()
    except requests.JSONDecodeError:
        pytest.fail(f"{label}: response was not JSON", pytrace=False)
    if payload.get("code") != code:
        pytest.fail(f"{label}: expected response code {code}, got {payload.get('code')}", pytrace=False)
    return payload


def _manage_key(client: RestClient, method: str, token: str, *, label: str, json=None):
    try:
        return client.request(method, "/system/tokens", json={"token": token, **(json or {})})
    except requests.RequestException:
        pytest.fail(f"{label}: request failed before a response was received", pytrace=False)


def _expire_created_key(token: str) -> None:
    """Test-only setup for an expiry state that the public API cannot create quickly."""
    try:
        records = list(ScopedAPITokenService.query(token=token))
        if len(records) != 1:
            pytest.fail("expiry setup did not find exactly one created scoped-key row", pytrace=False)
        tenant_id = records[0].tenant_id
        updated = ScopedAPITokenService.filter_update(
            [ScopedAPIToken.tenant_id == tenant_id, ScopedAPIToken.token == token],
            {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
        )
        if updated != 1:
            pytest.fail("expiry setup did not update exactly one created scoped-key row", pytrace=False)
    except pytest.fail.Exception:
        raise
    except Exception:
        pytest.fail("expiry setup could not update the created scoped-key row", pytrace=False)


@wait_for(20, 1, "Scoped retrieval did not return the seeded allowed chunk")
def _retrieval_contains_chunk(
    client: RestClient,
    question: str,
    expected_dataset_id: str,
    expected_chunk_id: str,
) -> bool:
    response = client.post("/retrieval", json={"question": question, "top_k": 10, "page_size": 10})
    payload = _expect_json(response, status=200, code=0, label="omitted-dataset retrieval")
    chunks = payload["data"]["chunks"]
    if expected_chunk_id not in {chunk["id"] for chunk in chunks}:
        return False
    assert all(chunk["dataset_id"] == expected_dataset_id for chunk in chunks)
    return True


@pytest.mark.p2
def test_scoped_api_key_public_lifecycle(auth, rest_client, tmp_path):
    """A retrieval key must work only for its allowlisted dataset for its whole lifecycle."""
    admin_client = RestClient(token=auth)
    suffix = uuid.uuid4().hex[:12]
    dataset_ids: list[str] = []
    scoped_token: str | None = None

    try:
        for scope_name in ("allowed", "other"):
            response = admin_client.post(
                "/datasets",
                json={"name": f"scoped-key-{scope_name}-{suffix}"},
            )
            payload = _expect_json(response, status=200, code=0, label=f"create {scope_name} dataset")
            dataset_ids.append(payload["data"]["id"])
        allowed_dataset_id, other_dataset_id = dataset_ids

        document_path = tmp_path / f"scoped-key-{suffix}.txt"
        chunk_content = f"SCOPED_API_KEY_BOUNDARY_{suffix}"
        document_path.write_text(chunk_content, encoding="utf-8")
        with document_path.open("rb") as file_obj:
            response = admin_client.post(
                f"/datasets/{allowed_dataset_id}/documents",
                files=[("file", (document_path.name, file_obj))],
            )
        payload = _expect_json(response, status=200, code=0, label="upload allowed document")
        document_id = payload["data"][0]["id"]

        response = admin_client.post(
            f"/datasets/{allowed_dataset_id}/documents/{document_id}/chunks",
            json={"content": chunk_content},
        )
        payload = _expect_json(response, status=200, code=0, label="seed allowed chunk")
        chunk_id = payload["data"]["chunk"]["id"]

        response = rest_client.post(
            "/system/tokens",
            json={
                "name": f"Scoped lifecycle {suffix}",
                "key_type": "retrieval",
                "allowed_dataset_ids": [allowed_dataset_id],
                "expires_in_days": 30,
            },
        )
        payload = _expect_json(response, status=200, code=0, label="create scoped key")
        scoped_token = payload["data"]["token"]
        scoped_client = RestClient(token=scoped_token)

        response = scoped_client.get("/datasets")
        payload = _expect_json(response, status=200, code=0, label="scoped dataset discovery")
        assert [dataset["id"] for dataset in payload["data"]] == [allowed_dataset_id]
        assert payload["total_datasets"] == 1

        response = scoped_client.get(f"/datasets/{allowed_dataset_id}/documents")
        payload = _expect_json(response, status=200, code=0, label="allowed document listing")
        assert payload["data"]["total"] == 1
        assert [document["id"] for document in payload["data"]["docs"]] == [document_id]

        response = scoped_client.get(f"/datasets/{other_dataset_id}/documents")
        _expect_json(response, status=403, code=403, label="other-dataset document denial")

        _retrieval_contains_chunk(scoped_client, chunk_content, allowed_dataset_id, chunk_id)

        response = scoped_client.post(
            "/retrieval",
            json={"question": chunk_content, "dataset_ids": [allowed_dataset_id], "top_k": 10},
        )
        payload = _expect_json(response, status=200, code=0, label="explicit allowed retrieval")
        assert chunk_id in {chunk["id"] for chunk in payload["data"]["chunks"]}
        assert all(chunk["dataset_id"] == allowed_dataset_id for chunk in payload["data"]["chunks"])

        for label, requested_ids in (
            ("other-dataset retrieval", [other_dataset_id]),
            ("mixed-dataset retrieval", [allowed_dataset_id, other_dataset_id]),
        ):
            response = scoped_client.post(
                "/retrieval",
                json={"question": chunk_content, "dataset_ids": requested_ids},
            )
            payload = _expect_json(response, status=403, code=403, label=label)
            assert "data" not in payload

        response = scoped_client.get("/system/status")
        _expect_json(response, status=403, code=403, label="system status denial")

        response = scoped_client.post("/datasets", json={"name": f"denied-{suffix}"})
        _expect_json(response, status=403, code=403, label="dataset write denial")

        response = _manage_key(
            rest_client,
            "PATCH",
            scoped_token,
            label="disable scoped key",
            json={"enabled": False},
        )
        _expect_json(response, status=200, code=0, label="disable scoped key")
        response = scoped_client.get("/datasets")
        _expect_json(response, status=401, code=401, label="disabled scoped key")

        response = _manage_key(
            rest_client,
            "PATCH",
            scoped_token,
            label="re-enable scoped key",
            json={"enabled": True},
        )
        _expect_json(response, status=200, code=0, label="re-enable scoped key")
        response = scoped_client.get("/datasets")
        payload = _expect_json(response, status=200, code=0, label="re-enabled scoped key")
        assert [dataset["id"] for dataset in payload["data"]] == [allowed_dataset_id]

        _expire_created_key(scoped_token)
        response = scoped_client.get("/datasets")
        _expect_json(response, status=401, code=401, label="expired scoped key")

        response = _manage_key(
            rest_client,
            "PATCH",
            scoped_token,
            label="restore scoped key expiry",
            json={"expires_in_days": 30},
        )
        _expect_json(response, status=200, code=0, label="restore scoped key expiry")
        response = scoped_client.get("/datasets")
        _expect_json(response, status=200, code=0, label="restored scoped key")

        response = _manage_key(rest_client, "DELETE", scoped_token, label="delete scoped key")
        _expect_json(response, status=200, code=0, label="delete scoped key")
        response = scoped_client.get("/datasets")
        _expect_json(response, status=401, code=401, label="deleted scoped key")
    finally:
        if scoped_token is not None:
            try:
                rest_client.delete("/system/tokens", json={"token": scoped_token})
            except requests.RequestException:
                pass
        if dataset_ids:
            try:
                admin_client.delete("/datasets", json={"ids": dataset_ids})
            except requests.RequestException:
                pass
