"""Study brief AI turn (heuristic path — no OpenAI key in tests)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth(client: TestClient) -> dict[str, str]:
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Brief User",
            "email": "brief.user@example.com",
            "password": "password123",
        },
    )
    assert reg.status_code == 201
    return {"Authorization": f"Bearer {reg.json()['access_token']}"}


def test_ai_turn_builds_brief_heuristic(client: TestClient) -> None:
    headers = _auth(client)
    project = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Pet Brand"}
    ).json()
    chat = client.post(
        f"/api/v1/projects/{project['id']}/chats",
        headers=headers,
        json={"title": "New Chat"},
    ).json()

    response = client.post(
        f"/api/v1/chats/{chat['id']}/ai-turn",
        headers=headers,
        json={
            "content": "I want to create a study on pet shedding logos",
            "attachment_urls": [],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user_message"]["role"] == "user"
    assert body["assistant_message"]["role"] == "assistant"
    assert body["study_brief"]["title"]
    assert body["phase"] in {"gathering", "brief_ready"}

    brief = client.get(
        f"/api/v1/chats/{chat['id']}/study-brief", headers=headers
    )
    assert brief.status_code == 200
    assert brief.json()["study_brief"]["title"]


def test_initial_greeting_does_not_invent_study(client: TestClient) -> None:
    headers = _auth(client)
    project = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Greeting Project"}
    ).json()
    chat = client.post(
        f"/api/v1/projects/{project['id']}/chats",
        headers=headers,
        json={"title": "New Chat"},
    ).json()

    response = client.post(
        f"/api/v1/chats/{chat['id']}/ai-turn",
        headers=headers,
        json={"content": "hi", "attachment_urls": []},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["phase"] == "gathering"
    assert body["study_brief"]["title"] == ""
    assert body["study_brief"]["categories"] == []
    assert "what would you like to learn or test" in body["assistant_message"][
        "content"
    ].lower()


def test_confirm_incomplete_brief_returns_validation_error(client: TestClient) -> None:
    headers = _auth(client)
    project = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Incomplete Project"}
    ).json()
    chat = client.post(
        f"/api/v1/projects/{project['id']}/chats",
        headers=headers,
        json={"title": "New Chat"},
    ).json()

    response = client.post(
        f"/api/v1/chats/{chat['id']}/study-brief/confirm",
        headers=headers,
    )

    assert response.status_code == 422, response.text
    assert "complete the study brief" in response.json()["detail"].lower()


def test_patch_study_brief_title(client: TestClient) -> None:
    headers = _auth(client)
    project = client.post(
        "/api/v1/projects", headers=headers, json={"title": "P"}
    ).json()
    chat = client.post(
        f"/api/v1/projects/{project['id']}/chats",
        headers=headers,
        json={},
    ).json()
    client.post(
        f"/api/v1/chats/{chat['id']}/ai-turn",
        headers=headers,
        json={"content": "Create a study on pet shedding", "attachment_urls": []},
    )
    patched = client.patch(
        f"/api/v1/chats/{chat['id']}/study-brief",
        headers=headers,
        json={"title": "Pet Shedding Logo Study"},
    )
    assert patched.status_code == 200
    assert patched.json()["study_brief"]["title"] == "Pet Shedding Logo Study"
