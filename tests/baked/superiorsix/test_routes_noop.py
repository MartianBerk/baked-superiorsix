from fastapi.testclient import TestClient
from tests.baked.superiorsix.tokens import auth_header, make_access_token

# Legacy supersix routes still returning scaffold no-ops.
PUBLIC_NOOP = [
    ("GET", "/health"),
    ("GET", "/game/specialmessage"),
]

IMPLEMENTED_PUBLIC = [
    ("GET", "/game/meta"),
    ("GET", "/game/livescores"),
    ("GET", "/game/currentround"),
    ("GET", "/game/livematches"),
    ("GET", "/game/matchdetail"),
    ("GET", "/game/listsubrounds"),
    ("GET", "/stats/aggregate"),
    ("GET", "/stats/winners"),
    ("GET", "/euros/matches"),
    ("GET", "/euros/scores"),
    ("GET", "/euros/listpredictions"),
    ("GET", "/eurowizard/listpredictions"),
    ("GET", "/euros/getpredictions"),
    ("GET", "/euros/meta"),
    ("GET", "/worldcup/meta"),
    ("GET", "/worldcup/matches"),
    ("GET", "/worldcup/scores"),
    ("GET", "/worldcup/listpredictions"),
    ("GET", "/qatarhero/listpredictions"),
    ("GET", "/worldcup/getpredictions"),
]

PROTECTED_NOOP = [
    ("GET", "/admin/getround", "admin"),
    ("POST", "/admin/setspecialmessage", "admin"),
    ("GET", "/admin/getspecialmessage", "admin"),
    ("GET", "/admin/endspecialmessage", "admin"),
]

PROTECTED_WORLDCUP = [
    ("GET", "/worldcup/getprediction", "worldcup"),
    ("POST", "/worldcup/addprediction", "worldcup"),
    ("GET", "/admin/worldcup/settings", "admin"),
    ("POST", "/admin/worldcup/settings", "admin"),
    ("POST", "/admin/worldcup/opentournament", "admin"),
    ("POST", "/admin/worldcup/endtournament", "admin"),
    ("GET", "/admin/worldcup/matches", "admin"),
]

PROTECTED_IMPLEMENTED = [
    ("GET", "/user/getprediction", "player"),
    ("POST", "/user/addprediction", "player"),
    ("POST", "/user/updatedetails", "player"),
    ("GET", "/admin/listleagues", "admin"),
    ("GET", "/admin/listteamxref", "admin"),
    ("POST", "/admin/upsertteamxref", "admin"),
    ("GET", "/admin/listrounds", "admin"),
    ("GET", "/admin/currentround", "admin"),
    ("GET", "/admin/historicrounds", "admin"),
    ("POST", "/admin/addround", "admin"),
    ("POST", "/admin/endround", "admin"),
    ("GET", "/admin/listsubrounds", "admin"),
    ("GET", "/admin/currentsubround", "admin"),
    ("POST", "/admin/opensubround", "admin"),
    ("POST", "/admin/undosubround", "admin"),
    ("GET", "/admin/listmatches", "admin"),
    ("GET", "/admin/getmatch", "admin"),
    ("POST", "/admin/addmatch", "admin"),
    ("POST", "/admin/addmatches", "admin"),
    ("GET", "/admin/dropmatch", "admin"),
    ("GET", "/admin/listpredictions", "admin"),
    ("GET", "/admin/listpredictionsnew", "admin"),
    ("POST", "/admin/addprediction", "admin"),
    ("POST", "/admin/addpredictions", "admin"),
    ("GET", "/admin/dropprediction", "admin"),
    ("GET", "/admin/listplayers", "admin"),
    ("POST", "/admin/inviteplayer", "admin"),
    ("GET", "/admin/getplayerroles", "admin"),
    ("POST", "/admin/updateroles", "admin"),
    ("POST", "/admin/addplayer", "admin"),
    ("GET", "/admin/dropplayer", "admin"),
    ("GET", "/admin/reactivateplayer", "admin"),
    ("GET", "/euros/getprediction", "euros"),
    ("POST", "/euros/addprediction", "euros"),
    ("GET", "/admin/euros/meta", "admin"),
    ("GET", "/admin/euros/matches", "admin"),
    ("POST", "/admin/euros/opentournament", "admin"),
    ("POST", "/admin/euros/endtournament", "admin"),
]


def _request(client: TestClient, method: str, path: str, headers: dict[str, str] | None = None):
    # Required query for getplayerroles / listpredictions so auth gating is exercised (not 422).
    get_kwargs: dict[str, object] = {}
    if path == "/admin/getplayerroles":
        get_kwargs["params"] = {"email": "roles@example.com"}
    elif path == "/admin/listpredictions":
        get_kwargs["params"] = {"authId": "00000000-0000-0000-0000-000000000001"}
    elif path == "/admin/dropprediction":
        get_kwargs["params"] = {"id": 1}
    elif path in {"/euros/getprediction", "/worldcup/getprediction"}:
        get_kwargs["params"] = {"gameId": 1}
    if path == "/admin/worldcup/settings" and method == "POST":
        return client.post(
            path,
            json={"buy_in_pence": 200, "participant_count": 14},
            headers=headers,
        )
    if method == "GET":
        return client.get(path, headers=headers, **get_kwargs)
    if path == "/admin/addprediction":
        return client.post(
            path,
            json={
                "auth_id": "00000000-0000-0000-0000-000000000001",
                "match_id": 1,
                "prediction": "home",
            },
            headers=headers,
        )
    if path == "/admin/addpredictions":
        return client.post(path, json=[], headers=headers)
    if path == "/admin/upsertteamxref":
        return client.post(
            path,
            json={"team_name": "Arsenal FC", "xref": "Arsenal"},
            headers=headers,
        )
    if path in {"/euros/addprediction", "/worldcup/addprediction"}:
        return client.post(
            path,
            json={"game_id": 1, "prediction": "home"},
            headers=headers,
        )
    if path == "/admin/euros/opentournament":
        return client.post(
            path,
            json={
                "start_date": "2024-06-01",
                "buy_in_pence": 0,
                "entrant_ids": ["00000000-0000-0000-0000-000000000001"],
            },
            headers=headers,
        )
    if path == "/admin/euros/endtournament":
        return client.post(
            path,
            json={"winner_ids": ["00000000-0000-0000-0000-000000000001"]},
            headers=headers,
        )
    if path == "/admin/worldcup/opentournament":
        return client.post(
            path,
            json={
                "buy_in_pence": 200,
                "entrant_ids": ["00000000-0000-0000-0000-000000000001"],
            },
            headers=headers,
        )
    if path == "/admin/worldcup/endtournament":
        return client.post(
            path,
            json={"winner_ids": ["00000000-0000-0000-0000-000000000001"]},
            headers=headers,
        )
    return client.post(path, json={}, headers=headers)


def test_public_noop_routes(client: TestClient) -> None:
    for method, path in PUBLIC_NOOP:
        response = _request(client, method, path)
        assert response.status_code == 200, f"{method} {path} -> {response.status_code}"
        if path != "/health":
            assert response.json() == {"noop": True}, path


def test_protected_noop_routes_require_auth(client: TestClient) -> None:
    for method, path, _role in PROTECTED_NOOP + PROTECTED_IMPLEMENTED + PROTECTED_WORLDCUP:
        response = _request(client, method, path)
        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"


def test_protected_noop_routes_with_role(client: TestClient) -> None:
    for method, path, role in PROTECTED_NOOP:
        token, _ = make_access_token(roles=[role])
        response = _request(client, method, path, headers=auth_header(token))
        assert response.status_code == 200, f"{method} {path} -> {response.status_code}"
        assert response.json() == {"noop": True}, path


def test_worldcup_protected_routes_accept_role(client: TestClient) -> None:
    """World Cup gated routes are implemented (not noop) but still role-gated."""
    for method, path, role in PROTECTED_WORLDCUP:
        token, _ = make_access_token(roles=[role])
        response = _request(client, method, path, headers=auth_header(token))
        # Missing query/body → 400/422; auth must succeed first.
        assert response.status_code != 401, f"{method} {path} -> {response.status_code}"
        assert response.status_code != 403, f"{method} {path} -> {response.status_code}"
        assert response.json() != {"noop": True}, path


def test_openapi_lists_expected_paths(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    protected = PROTECTED_NOOP + PROTECTED_IMPLEMENTED + PROTECTED_WORLDCUP
    implemented = [(m, p) for m, p, _ in protected]
    for method, path in PUBLIC_NOOP + IMPLEMENTED_PUBLIC + implemented:
        assert path in paths, f"missing {path}"
        assert method.lower() in paths[path], f"missing {method} {path}"
