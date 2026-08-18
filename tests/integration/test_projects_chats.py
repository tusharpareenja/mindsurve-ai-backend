"""Projects and chats API tests."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth_headers(client: TestClient) -> dict[str, str]:
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Ada Lovelace",
            "email": "ada@example.com",
            "password": "securepass1",
        },
    )
    assert reg.status_code == 201
    token = reg.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_and_list_projects(client: TestClient) -> None:
    headers = _auth_headers(client)
    created = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "  Brand Launch  "},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "Brand Launch"
    assert body["workflow_type"] == "beginner"
    assert body["status"] == "CREATED"
    assert body["description"] == ""

    listed = client.get("/api/v1/projects", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == body["id"]


def test_create_project_empty_title(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.post("/api/v1/projects", headers=headers, json={"title": "   "})
    assert response.status_code == 422


def test_project_requires_auth(client: TestClient) -> None:
    response = client.get("/api/v1/projects")
    assert response.status_code == 401


def test_rename_and_delete_project(client: TestClient) -> None:
    headers = _auth_headers(client)
    created = client.post("/api/v1/projects", headers=headers, json={"title": "Old"})
    project_id = created.json()["id"]

    renamed = client.patch(
        f"/api/v1/projects/{project_id}",
        headers=headers,
        json={"title": "New Name"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "New Name"

    deleted = client.delete(f"/api/v1/projects/{project_id}", headers=headers)
    assert deleted.status_code == 200

    missing = client.get(f"/api/v1/projects/{project_id}", headers=headers)
    assert missing.status_code == 404


def test_chat_flow_and_messages(client: TestClient) -> None:
    headers = _auth_headers(client)
    project = client.post("/api/v1/projects", headers=headers, json={"title": "P1"}).json()
    project_id = project["id"]

    chat = client.post(
        f"/api/v1/projects/{project_id}/chats",
        headers=headers,
        json={},
    )
    assert chat.status_code == 201
    chat_id = chat.json()["id"]
    assert chat.json()["title"] == "New Chat"

    msg = client.post(
        f"/api/v1/chats/{chat_id}/messages",
        headers=headers,
        json={"content": "Hello research", "role": "user"},
    )
    assert msg.status_code == 201
    assert msg.json()["content"] == "Hello research"

    assistant = client.post(
        f"/api/v1/chats/{chat_id}/messages",
        headers=headers,
        json={"content": "Hi there", "role": "assistant"},
    )
    assert assistant.status_code == 201

    messages = client.get(f"/api/v1/chats/{chat_id}/messages", headers=headers)
    assert messages.status_code == 200
    assert len(messages.json()["items"]) == 2
    assert messages.json()["items"][0]["role"] == "user"
    assert messages.json()["has_more"] is False

    chats = client.get(f"/api/v1/projects/{project_id}/chats", headers=headers)
    assert chats.status_code == 200
    assert len(chats.json()) == 1
    assert chats.json()[0]["last_message_preview"] == "Hi there"


def test_start_chat_with_message(client: TestClient) -> None:
    headers = _auth_headers(client)
    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "P2"}
    ).json()["id"]

    started = client.post(
        f"/api/v1/projects/{project_id}/chats/start",
        headers=headers,
        json={"content": "I want a logo study"},
    )
    assert started.status_code == 201
    data = started.json()
    assert data["chat"]["last_message_preview"] == "I want a logo study"
    assert data["message"]["role"] == "user"

    renamed = client.patch(
        f"/api/v1/chats/{data['chat']['id']}",
        headers=headers,
        json={"title": "Logo study"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Logo study"


def test_message_history_uses_cursor_pagination(client: TestClient) -> None:
    headers = _auth_headers(client)
    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Long chat"}
    ).json()["id"]
    chat_id = client.post(
        f"/api/v1/projects/{project_id}/chats",
        headers=headers,
        json={"title": "History"},
    ).json()["id"]

    for index in range(7):
        response = client.post(
            f"/api/v1/chats/{chat_id}/messages",
            headers=headers,
            json={"content": f"Message {index}", "role": "user"},
        )
        assert response.status_code == 201

    newest = client.get(
        f"/api/v1/chats/{chat_id}/messages?limit=3", headers=headers
    )
    assert newest.status_code == 200
    newest_body = newest.json()
    assert [item["content"] for item in newest_body["items"]] == [
        "Message 4",
        "Message 5",
        "Message 6",
    ]
    assert newest_body["has_more"] is True
    assert newest_body["next_before"]

    older = client.get(
        f"/api/v1/chats/{chat_id}/messages",
        headers=headers,
        params={"limit": 3, "before": newest_body["next_before"]},
    )
    assert older.status_code == 200
    older_body = older.json()
    assert [item["content"] for item in older_body["items"]] == [
        "Message 1",
        "Message 2",
        "Message 3",
    ]
    assert older_body["has_more"] is True


def test_cannot_access_other_users_project(client: TestClient) -> None:
    headers_a = _auth_headers(client)
    project_id = client.post(
        "/api/v1/projects", headers=headers_a, json={"title": "Secret"}
    ).json()["id"]

    reg_b = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Bob",
            "email": "bob@example.com",
            "password": "securepass1",
        },
    )
    headers_b = {"Authorization": f"Bearer {reg_b.json()['access_token']}"}

    response = client.get(f"/api/v1/projects/{project_id}", headers=headers_b)
    assert response.status_code == 404

    chats = client.get(f"/api/v1/projects/{project_id}/chats", headers=headers_b)
    assert chats.status_code == 404


def test_delete_project_cascades_chats(client: TestClient) -> None:
    headers = _auth_headers(client)
    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Cascade"}
    ).json()["id"]
    started = client.post(
        f"/api/v1/projects/{project_id}/chats/start",
        headers=headers,
        json={"content": "msg"},
    ).json()
    chat_id = started["chat"]["id"]

    client.delete(f"/api/v1/projects/{project_id}", headers=headers)

    assert client.get(f"/api/v1/chats/{chat_id}", headers=headers).status_code == 404
    assert client.get(f"/api/v1/chats/{chat_id}/messages", headers=headers).status_code == 404


def test_list_all_chats(client: TestClient) -> None:
    headers = _auth_headers(client)
    p1 = client.post("/api/v1/projects", headers=headers, json={"title": "A"}).json()["id"]
    p2 = client.post("/api/v1/projects", headers=headers, json={"title": "B"}).json()["id"]
    client.post(f"/api/v1/projects/{p1}/chats", headers=headers, json={"title": "c1"})
    client.post(f"/api/v1/projects/{p2}/chats", headers=headers, json={"title": "c2"})

    all_chats = client.get("/api/v1/chats", headers=headers)
    assert all_chats.status_code == 200
    assert len(all_chats.json()) == 2


def test_move_and_delete_chat(client: TestClient) -> None:
    headers = _auth_headers(client)
    started = client.post(
        "/api/v1/chats/start",
        headers=headers,
        json={"content": "Inbox idea"},
    )
    assert started.status_code == 201
    chat_id = started.json()["chat"]["id"]
    inbox_id = started.json()["chat"]["project_id"]

    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "Campaign"}
    ).json()["id"]

    moved = client.patch(
        f"/api/v1/chats/{chat_id}",
        headers=headers,
        json={"project_id": project_id},
    )
    assert moved.status_code == 200
    assert moved.json()["project_id"] == project_id

    in_project = client.get(f"/api/v1/projects/{project_id}/chats", headers=headers)
    assert in_project.status_code == 200
    assert any(row["id"] == chat_id for row in in_project.json())

    still_inbox = client.get(f"/api/v1/projects/{inbox_id}/chats", headers=headers)
    assert still_inbox.status_code == 200
    assert all(row["id"] != chat_id for row in still_inbox.json())

    deleted = client.delete(f"/api/v1/chats/{chat_id}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/chats/{chat_id}", headers=headers).status_code == 404
