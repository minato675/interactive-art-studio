import pytest
from test_runtime_pipeline import runtime_client, verified  # noqa: F401 — fixture


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_review_queue_idempotency_and_publication_gate(runtime_client, decision):  # noqa: F811
    client = runtime_client
    _, job = verified(client, "review")
    assert client.get("/infra/runtime/reviews").json()[0]["id"] == job["id"]
    endpoint = f"/infra/runtime/jobs/{job['id']}/review"
    body = {"decision": decision, "reviewer": "operator", "reason": "합성 검수", "expected_attempt": job["attempt"]}
    assert client.post(endpoint, json={**body, "expected_attempt": job["attempt"] + 1}).status_code == 409
    assert client.post(endpoint, json=body, headers={"X-Operator-Token": "wrong"}).status_code == 401
    response = client.post(endpoint, json=body)
    assert response.status_code == 200
    assert response.json()["result"]["human_review"]["request"]["decision"] == decision
    assert client.post(endpoint, json=body).status_code == 200
    assert client.post(endpoint, json={**body, "reason": "변경"}).status_code == 409
    assert client.get("/infra/runtime/reviews").json() == []
    assert not any(j["kind"] == "ue_publish" for j in client.get("/infra/jobs").json())
    approval = {"request_id": "publish-reviewed", "target_node": "show", "reviewer": "operator", "reason": "게시 검수"}
    response = client.post(f"/infra/jobs/{job['id']}/approve-publication", json=approval)
    assert response.status_code == (202 if decision == "approve" else 409)


def test_publication_approval_records_review_and_removes_pending(runtime_client):  # noqa: F811
    client = runtime_client
    _, job = verified(client, "review")
    approval = {"request_id": "review-and-publish", "target_node": "show", "reviewer": "operator", "reason": "캡처 검수"}
    assert client.post(f"/infra/jobs/{job['id']}/approve-publication", json=approval).status_code == 202
    result = client.get(f"/infra/jobs/{job['id']}").json()["result"]
    assert result["review_state"] == "human_review_approved"
    assert result["human_review"]["request"]["reason"] == "캡처 검수"
    assert client.get("/infra/runtime/reviews").json() == []
