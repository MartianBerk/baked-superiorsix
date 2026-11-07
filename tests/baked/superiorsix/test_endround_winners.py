from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
from tests.baked.superiorsix.tokens import auth_header, make_access_token


def _admin_headers() -> dict[str, str]:
    token, _ = make_access_token(roles=["admin"])
    return auth_header(token)


def test_endround_records_multiple_winners(client: TestClient) -> None:
    admin = _admin_headers()
    opened = client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": "2026-01-01", "buy_in_pence": 100},
    )
    assert opened.status_code == 200, opened.text
    round_id = opened.json()["id"]
    assert opened.json()["winner_ids"] == []

    winner_a = uuid4()
    winner_b = uuid4()
    ended = client.post(
        "/admin/endround",
        headers=admin,
        params={"endDate": "15-01-2026"},
        json={"winner_ids": [str(winner_a), str(winner_b)]},
    )
    assert ended.status_code == 200, ended.text
    body = ended.json()
    assert body["id"] == round_id
    assert body["end_date"] == "2026-01-15"
    assert set(body["winner_ids"]) == {str(winner_a), str(winner_b)}

    listed = client.get("/admin/listrounds", headers=admin)
    assert listed.status_code == 200
    rounds = listed.json()["rounds"]
    assert len(rounds) == 1
    assert set(rounds[0]["winner_ids"]) == {str(winner_a), str(winner_b)}

    current = client.get("/admin/currentround", headers=admin)
    assert current.status_code == 200
    assert current.json()["current_round"] is None


def test_endround_requires_end_date(client: TestClient) -> None:
    admin = _admin_headers()
    client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": "2026-01-01", "buy_in_pence": 50},
    )
    response = client.post(
        "/admin/endround",
        headers=admin,
        json={"winner_ids": [str(uuid4())]},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "missing endDate"


def test_endround_rejects_empty_winners(client: TestClient) -> None:
    admin = _admin_headers()
    client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": "2026-01-01", "buy_in_pence": 50},
    )
    response = client.post(
        "/admin/endround",
        headers=admin,
        params={"endDate": "15-01-2026"},
        json={"winner_ids": []},
    )
    assert response.status_code == 422


def test_endround_rejects_missing_winners_body(client: TestClient) -> None:
    admin = _admin_headers()
    client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": "2026-01-01", "buy_in_pence": 50},
    )
    response = client.post(
        "/admin/endround",
        headers=admin,
        params={"endDate": "15-01-2026"},
        json={},
    )
    assert response.status_code == 422


def test_endround_rejects_duplicate_winners(client: TestClient) -> None:
    admin = _admin_headers()
    client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": "2026-01-01", "buy_in_pence": 50},
    )
    winner = uuid4()
    response = client.post(
        "/admin/endround",
        headers=admin,
        params={"endDate": "15-01-2026"},
        json={"winner_ids": [str(winner), str(winner)]},
    )
    assert response.status_code == 400
    assert "unique" in response.json()["detail"]


def test_endround_rejects_when_no_open_round(client: TestClient) -> None:
    admin = _admin_headers()
    response = client.post(
        "/admin/endround",
        headers=admin,
        params={"endDate": "15-01-2026"},
        json={"winner_ids": [str(uuid4())]},
    )
    assert response.status_code == 404


def test_endround_single_winner(client: TestClient) -> None:
    admin = _admin_headers()
    client.post(
        "/admin/addround",
        headers=admin,
        json={"start_date": date.today().isoformat(), "buy_in_pence": 100},
    )
    winner = uuid4()
    ended = client.post(
        "/admin/endround",
        headers=admin,
        params={"endDate": date.today().strftime("%d-%m-%Y")},
        json={"winner_ids": [str(winner)]},
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["winner_ids"] == [str(winner)]
