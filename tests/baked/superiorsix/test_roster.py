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
            "first_name": "A",
            "last_name": "B",
            "email": "xavier@example.com",
        },
    )
    assert response.status_code == 403


def test_list_players_requires_admin(client: TestClient) -> None:
    token, _ = make_access_token(roles=["player"])
    listed = client.get("/admin/listplayers", headers=auth_header(token))
    assert listed.status_code == 403


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
            "email": "bad@example.com",
            "roles": ["player", "superuser"],
        },
    )
    assert response.status_code == 422


def test_inviteplayer_requires_email(client: TestClient) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    response = client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
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
        json={"email": "needsmail@example.com"},
    )
    response = client.post(
        "/admin/addplayer",
        headers=admin,
        json={
            "auth_id": str(uuid4()),
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
            "first_name": "Martin",
            "last_name": "Berk",
            "email": "martin@example.com",
            "roles": ["player", "euros"],
        },
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["roles"] == ["player", "euros", "user-profile"]
    assert "martin@example.com" in auth_admin.created_invite_ready
    assert auth_admin.passwords["martin@example.com"] is None
    assert auth_admin.memberships["martin@example.com"] == ["player", "euros", "user-profile"]


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
            "first_name": "Martin",
            "last_name": "Berk",
            "email": "martin@example.com",
            "roles": ["player", "euros"],
        },
    )
    assert invited.status_code == 201, invited.text

    auth_admin.set_password("martin@example.com", "password123")
    auth_id = uuid4()
    user_profiles.bind_email("martin@example.com", auth_id)

    accepted = client.post(
        "/user/acceptinvite",
        json={
            "email": "martin@example.com",
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
    assert body["access_token"].startswith("token:martin@example.com:superiorsix")
    assert user_profiles._invites["martin@example.com"].status == "accepted"


def test_inviteplayer_always_grants_user_profile(
    client: TestClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    invited = client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "email": "np@example.com",
            "roles": ["player", "worldcup"],
        },
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["roles"] == ["player", "worldcup", "user-profile"]
    assert auth_admin.memberships["np@example.com"] == [
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
            "first_name": "Leg",
            "last_name": "Acy",
            "email": "legacy@example.com",
            "roles": ["player"],
        },
    )
    # Simulate a pre-always-grant invite stored without user-profile.
    stored = user_profiles._invites["legacy@example.com"]
    user_profiles._invites["legacy@example.com"] = type(stored)(
        id=stored.id,
        email=stored.email,
        status=stored.status,
        auth_id=stored.auth_id,
        profile=dict(stored.profile),
        roles=["player"],
    )

    auth_admin.set_password("legacy@example.com", "password123")
    auth_id = uuid4()
    user_profiles.bind_email("legacy@example.com", auth_id)

    accepted = client.post(
        "/user/acceptinvite",
        json={"email": "legacy@example.com", "password": "password123"},
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
            "roles": ["player"],
            "first_name": "M",
            "last_name": "B",
            "email": "martin@example.com",
        },
    )
    auth_admin.set_password("martin@example.com", "password123")

    denied = client.post(
        "/user/acceptinvite",
        json={"email": "martin@example.com", "password": "wrong-password"},
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
            "roles": ["player"],
            "email": "martin@example.com",
        },
    )
    assert auth_admin.passwords["martin@example.com"] is None

    denied = client.post(
        "/user/acceptinvite",
        json={"email": "martin@example.com", "password": "password123"},
    )
    assert denied.status_code == 401


def test_accept_invite_requires_pending_invite(
    client: TestClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    auth_admin.register("nobody@example.com", "password123")
    auth_admin.memberships["nobody@example.com"] = ["player", "user-profile"]
    response = client.post(
        "/user/acceptinvite",
        json={"email": "nobody@example.com", "password": "password123"},
    )
    assert response.status_code == 403


def test_inviteplayer_rejects_game_master_role(client: TestClient) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    invited = client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "email": "gm-invite@example.com",
            "first_name": "Game",
            "last_name": "Master",
            "roles": ["player", "game-master"],
        },
    )
    assert invited.status_code == 422


def test_update_and_get_player_roles_can_assign_and_clear_admin(
    client: TestClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    admin = auth_header(admin_token)
    email = "ops-edit@example.com"
    auth_admin.memberships[email] = ["player", "user-profile"]

    got = client.get(
        "/admin/getplayerroles",
        headers=admin,
        params={"email": email},
    )
    assert got.status_code == 200, got.text
    assert got.json() == {
        "email": email,
        "roles": ["player", "user-profile"],
    }

    granted = client.post(
        "/admin/updateroles",
        headers=admin,
        json={"email": email, "roles": ["player", "worldcup", "euros", "admin"]},
    )
    assert granted.status_code == 200, granted.text
    roles = granted.json()["roles"]
    assert "admin" in roles
    assert "player" in roles
    assert "worldcup" in roles
    assert "euros" in roles
    assert "user-profile" in roles
    assert "game-master" not in roles
    assert auth_admin.memberships[email] == roles

    cleared = client.post(
        "/admin/updateroles",
        headers=admin,
        json={"email": email, "roles": ["player", "worldcup", "euros"]},
    )
    assert cleared.status_code == 200, cleared.text
    cleared_roles = cleared.json()["roles"]
    assert "admin" not in cleared_roles
    assert "player" in cleared_roles
    assert "user-profile" in cleared_roles
    assert auth_admin.memberships[email] == cleared_roles


def test_inviteplayer_accepts_admin_role(
    client: TestClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    response = client.post(
        "/admin/inviteplayer",
        headers=auth_header(admin_token),
        json={
            "email": "new-admin@example.com",
            "first_name": "New",
            "last_name": "Admin",
            "roles": ["player", "admin"],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "admin" in body["roles"]
    assert "player" in body["roles"]
    assert "user-profile" in body["roles"]
    assert auth_admin.memberships["new-admin@example.com"] == body["roles"]


def test_getplayerroles_requires_admin(client: TestClient) -> None:
    token, _ = make_access_token(roles=["player"])
    response = client.get(
        "/admin/getplayerroles",
        headers=auth_header(token),
        params={"email": "x@example.com"},
    )
    assert response.status_code == 403


def test_roster_admin_auth_reset_and_inactive(
    client: TestClient,
    auth_admin: FakeAuthAdminClient,
) -> None:
    admin_token, _ = make_access_token(roles=["admin"])
    admin = auth_header(admin_token)
    auth_id = uuid4()
    email = "auth-ops@example.com"

    client.post(
        "/admin/inviteplayer",
        headers=admin,
        json={"email": email, "first_name": "Auth", "last_name": "Ops", "roles": ["player"]},
    )
    auth_admin.link_auth_id(email, auth_id)
    auth_admin.passwords[email] = "password123"
    auth_admin.is_active[email] = True

    client.post(
        "/admin/addplayer",
        headers=admin,
        json={
            "auth_id": str(auth_id),
            "first_name": "Auth",
            "last_name": "Ops",
            "email": email,
        },
    )

    auth_state = client.get(f"/admin/players/{auth_id}/auth", headers=admin)
    assert auth_state.status_code == 200, auth_state.text
    assert auth_state.json()["must_set_password"] is False
    assert auth_state.json()["is_active"] is True

    reset = client.post(f"/admin/players/{auth_id}/reset-password", headers=admin)
    assert reset.status_code == 200, reset.text
    assert reset.json()["must_set_password"] is True
    assert auth_admin.passwords[email] is None

    inactive = client.patch(
        f"/admin/players/{auth_id}/auth",
        headers=admin,
        json={"is_active": False},
    )
    assert inactive.status_code == 200, inactive.text
    assert inactive.json()["is_active"] is False
    assert auth_admin.is_active[email] is False
