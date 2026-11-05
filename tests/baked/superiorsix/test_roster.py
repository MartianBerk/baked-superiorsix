from uuid import uuid4

from fastapi.testclient import TestClient
from tests.baked.superiorsix.fakes import FakeAuthAdminClient, FakeUserProfileClient
from tests.baked.superiorsix.tokens import auth_header, make_access_token


def test_list_add_drop_reactivate_player(client: TestClient) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    admin = auth_header(admin_token)
    auth_id = uuid4()

    empty = client.get("/admin/listplayers", headers=admin)
    assert empty.status_code == 200
    assert empty.json() == {"players": []}

    invited = client.post(
        "/admin/inviteplayer",
        headers=admin,
        json={
            "username": "ada",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "roles": ["player"],
        },
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["status"] == "pending"
    # user-profile is always granted even when not selected.
    assert invited.json()["roles"] == ["player", "user-profile"]
    assert invited.json()["profile"]["email"] == "ada@example.com"

    created = client.post(
        "/admin/addplayer",
        headers=admin,
        json={
            "auth_id": str(auth_id),
            "username": "ada",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "nickname": "Ada",
            "email": "ada@example.com",
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


def test_addplayer_requires_invite(client: TestClient) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    response = client.post(
        "/admin/addplayer",
        headers=auth_header(admin_token),
        json={
            "auth_id": str(uuid4()),
            "username": "nobody",
            "first_name": "A",
            "last_name": "B",
            "email": "nobody@example.com",
        },
    )
    assert response.status_code == 403


def test_addplayer_requires_admin(client: TestClient) -> None:
    token, _ = make_access_token(roles=["player"])
    response = client.post(
        "/admin/addplayer",
        headers=auth_header(token),
        json={
            "auth_id": str(uuid4()),
            "username": "xavier",
            "first_name": "A",
            "last_name": "B",
            "email": "xavier@example.com",
        },
    )
    assert response.status_code == 403


def test_game_master_can_list_players(client: TestClient) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    auth_id = uuid4()
    client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={"username": "gmplayer", "email": "gm@example.com"},
    )
    created = client.post(
        "/admin/addplayer",
        headers=auth_header(admin_token),
        json={
            "auth_id": str(auth_id),
            "username": "gmplayer",
            "first_name": "A",
            "last_name": "B",
            "email": "gm@example.com",
        },
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
    # BFF gates on active roster player; JWT still carries user-profile for baked-user.
    token, subject = make_access_token(
        auth_id=auth_id, roles=["player", "user-profile"]
    )
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


def test_inviteplayer_rejects_unknown_roles(client: TestClient) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    response = client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "username": "badroles",
            "email": "bad@example.com",
            "roles": ["player", "admin"],
        },
    )
    assert response.status_code == 422


def test_inviteplayer_requires_email(client: TestClient) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    response = client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "username": "noemail",
            "first_name": "No",
            "last_name": "Email",
            "roles": ["player"],
        },
    )
    assert response.status_code == 422


def test_addplayer_requires_email(client: TestClient) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    admin = auth_header(admin_token)
    client.post(
        "/admin/inviteplayer",
        headers=admin,
        json={"username": "needsmail", "email": "seed@example.com"},
    )
    response = client.post(
        "/admin/addplayer",
        headers=admin,
        json={
            "auth_id": str(uuid4()),
            "username": "needsmail",
            "first_name": "Needs",
            "last_name": "Mail",
        },
    )
    assert response.status_code == 422


def test_inviteplayer_creates_auth_user_and_membership(
    client: TestClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    invited = client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "username": "martin",
            "first_name": "Martin",
            "last_name": "Berk",
            "email": "martin@example.com",
            "roles": ["player", "euros"],
        },
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["roles"] == ["player", "euros", "user-profile"]
    assert "martin" in auth_admin.created_invite_ready
    assert auth_admin.passwords["martin"] is None
    assert auth_admin.memberships["martin"] == ["player", "euros", "user-profile"]


def test_accept_invite_after_set_password_creates_roster(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    invited = client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "username": "martin",
            "first_name": "Martin",
            "last_name": "Berk",
            "email": "martin@example.com",
            "roles": ["player", "euros"],
        },
    )
    assert invited.status_code == 201, invited.text

    auth_admin.set_password("martin", "password123")
    auth_id = uuid4()
    user_profiles.bind_username("martin", auth_id)

    accepted = client.post(
        "/user/acceptinvite",
        json={
            "username": "martin",
            "password": "password123",
            "nickname": "Marty",
        },
    )
    assert accepted.status_code == 201, accepted.text
    body = accepted.json()
    assert body["roles"] == ["player", "euros", "user-profile"]
    assert body["player"]["auth_id"] == str(auth_id)
    assert body["player"]["first_name"] == "Martin"
    assert body["player"]["nickname"] == "Marty"
    assert body["player"]["email"] == "martin@example.com"
    assert body["access_token"].startswith("token:martin:superiorsix")
    assert user_profiles._invites["martin"].status == "accepted"


def test_inviteplayer_always_grants_user_profile(
    client: TestClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    invited = client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "username": "noprofilechip",
            "email": "np@example.com",
            "roles": ["player", "worldcup"],
        },
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["roles"] == ["player", "worldcup", "user-profile"]
    assert auth_admin.memberships["noprofilechip"] == [
        "player",
        "worldcup",
        "user-profile",
    ]


def test_accept_invite_merges_user_profile_from_consumed_roles(
    client: TestClient,
    user_profiles: FakeUserProfileClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    """Even a legacy invite missing user-profile gets it on accept response."""
    admin_token, _ = make_access_token(roles=["admin"])
    client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "username": "legacy",
            "first_name": "Leg",
            "last_name": "Acy",
            "email": "legacy@example.com",
            "roles": ["player"],
        },
    )
    # Simulate a pre-always-grant invite stored without user-profile.
    stored = user_profiles._invites["legacy"]
    user_profiles._invites["legacy"] = type(stored)(
        id=stored.id,
        username=stored.username,
        status=stored.status,
        auth_id=stored.auth_id,
        profile=dict(stored.profile),
        roles=["player"],
    )

    auth_admin.set_password("legacy", "password123")
    auth_id = uuid4()
    user_profiles.bind_username("legacy", auth_id)

    accepted = client.post(
        "/user/acceptinvite",
        json={"username": "legacy", "password": "password123"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["roles"] == ["player", "user-profile"]


def test_accept_invite_rejects_bad_password(
    client: TestClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "username": "martin",
            "roles": ["player"],
            "first_name": "M",
            "last_name": "B",
            "email": "martin@example.com",
        },
    )
    auth_admin.set_password("martin", "password123")

    denied = client.post(
        "/user/acceptinvite",
        json={"username": "martin", "password": "wrong-password"},
    )
    assert denied.status_code == 401


def test_accept_invite_rejects_before_set_password(
    client: TestClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "username": "martin",
            "roles": ["player"],
            "email": "martin@example.com",
        },
    )
    assert auth_admin.passwords["martin"] is None

    denied = client.post(
        "/user/acceptinvite",
        json={"username": "martin", "password": "password123"},
    )
    assert denied.status_code == 401


def test_accept_invite_requires_pending_invite(
    client: TestClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    auth_admin.register("nobody", "password123")
    auth_admin.memberships["nobody"] = ["player", "user-profile"]
    response = client.post(
        "/user/acceptinvite",
        json={"username": "nobody", "password": "password123"},
    )
    assert response.status_code == 403
