from uuid import uuid4

from fastapi.testclient import TestClient
from tests.baked.superiorsix.fakes import FakeUserProfileClient
from tests.baked.superiorsix.tokens import auth_header, make_access_token


def test_list_add_drop_reactivate_player(client: TestClient) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    admin = auth_header(admin_token)
    auth_id = uuid4()

    empty = client.get("/admin/listplayers", headers=admin)
    assert empty.status_code == 200
    assert empty.json() == {"players": []}

    created = client.post(
        "/admin/addplayer",
        headers=admin,
        json={
            "auth_id": str(auth_id),
            "first_name": "Ada",
            "last_name": "Lovelace",
            "nickname": "Ada",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["auth_id"] == str(auth_id)
    assert body["first_name"] == "Ada"
    assert body["active"] is True
    assert body["retired"] is False

    listed = client.get("/admin/listplayers", headers=admin)
    assert listed.status_code == 200
    assert len(listed.json()["players"]) == 1

    dropped = client.get(
        "/admin/dropplayer",
        headers=admin,
        params={"authId": str(auth_id)},
    )
    assert dropped.status_code == 200
    assert dropped.json()["active"] is False
    assert dropped.json()["retired"] is True

    active_only = client.get("/admin/listplayers", headers=admin, params={"active": True})
    assert active_only.json()["players"] == []

    reactivated = client.get(
        "/admin/reactivateplayer",
        headers=admin,
        params={"authId": str(auth_id)},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["active"] is True


def test_addplayer_requires_admin(client: TestClient) -> None:
    token, _ = make_access_token(roles=["player"])
    response = client.post(
        "/admin/addplayer",
        headers=auth_header(token),
        json={"auth_id": str(uuid4()), "first_name": "A", "last_name": "B"},
    )
    assert response.status_code == 403


def test_game_master_can_list_players(client: TestClient) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    auth_id = uuid4()
    created = client.post(
        "/admin/addplayer",
        headers=auth_header(admin_token),
        json={"auth_id": str(auth_id), "first_name": "A", "last_name": "B"},
    )
    assert created.status_code == 201
    gm_token, _ = make_access_token(roles=["game-master"])
    listed = client.get("/admin/listplayers", headers=auth_header(gm_token))
    assert listed.status_code == 200
    assert listed.json()["players"][0]["auth_id"] == str(auth_id)


def test_update_details_patches_profile(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
) -> None:
    auth_id = uuid4()
    user_profiles.seed(auth_id, first_name="Grace", last_name="Hopper")
    token, subject = make_access_token(auth_id=auth_id, roles=["user-profile"])
    assert subject == auth_id
    response = client.post(
        "/user/updatedetails",
        headers=auth_header(token),
        json={"nickname": "Amazing"},
    )
    assert response.status_code == 200
    assert response.json()["nickname"] == "Amazing"
    assert response.json()["first_name"] == "Grace"


def test_dropplayer_requires_auth_id(client: TestClient) -> None:
    token, _ = make_access_token(roles=["admin"])
    response = client.get("/admin/dropplayer", headers=auth_header(token))
    assert response.status_code == 400
