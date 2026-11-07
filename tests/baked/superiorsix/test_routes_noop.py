from fastapi.testclient import TestClient
from tests.baked.superiorsix.tokens import auth_header, make_access_token

# Legacy supersix routes still returning scaffold no-ops.
PUBLIC_NOOP = [
    ("GET", "/health"),
    ("GET", "/game/meta"),
    ("GET", "/game/livescores"),
    ("GET", "/game/currentround"),
    ("GET", "/game/specialmessage"),
    ("GET", "/stats/aggregate"),
    ("GET", "/stats/winners"),
    ("GET", "/euros/matches"),
    ("GET", "/euros/scores"),
    ("GET", "/euros/listpredictions"),
    ("GET", "/eurowizard/listpredictions"),
    ("GET", "/euros/getpredictions"),
    ("GET", "/worldcup/matches"),
    ("GET", "/worldcup/scores"),
    ("GET", "/worldcup/listpredictions"),
    ("GET", "/qatarhero/listpredictions"),
    ("GET", "/worldcup/getpredictions"),
]

IMPLEMENTED_PUBLIC = [
    ("GET", "/game/livematches"),
    ("GET", "/game/matchdetail"),
    ("GET", "/game/listsubrounds"),
]

PROTECTED_NOOP = [
    ("GET", "/admin/historicrounds", "admin"),
    ("GET", "/admin/listpredictions", "admin"),
    ("GET", "/admin/listpredictionsnew", "admin"),
    ("POST", "/admin/addprediction", "admin"),
    ("GET", "/admin/getround", "admin"),
    ("POST", "/admin/addpredictions", "admin"),
    ("GET", "/admin/dropprediction", "admin"),
    ("POST", "/admin/setspecialmessage", "admin"),
    ("GET", "/admin/getspecialmessage", "admin"),
    ("GET", "/admin/endspecialmessage", "admin"),
    ("GET", "/euros/getprediction", "euros"),
    ("POST", "/euros/addprediction", "euros"),
    ("GET", "/worldcup/getprediction", "worldcup"),
    ("POST", "/worldcup/addprediction", "worldcup"),
]

PROTECTED_IMPLEMENTED = [
    ("GET", "/user/getprediction", "player"),
    ("POST", "/user/addprediction", "player"),
    ("POST", "/user/updatedetails", "player"),
    ("GET", "/admin/listleagues", "admin"),
    ("GET", "/admin/listrounds", "admin"),
    ("GET", "/admin/currentround", "admin"),
    ("POST", "/admin/addround", "admin"),
    ("POST", "/admin/endround", "admin"),
    ("GET", "/admin/listsubrounds", "admin"),
    ("GET", "/admin/currentsubround", "admin"),
    ("POST", "/admin/opensubround", "admin"),
    ("GET", "/admin/listmatches", "admin"),
    ("GET", "/admin/getmatch", "admin"),
    ("POST", "/admin/addmatch", "admin"),
    ("POST", "/admin/addmatches", "admin"),
    ("GET", "/admin/dropmatch", "admin"),
    ("GET", "/admin/listplayers", "admin"),
    ("POST", "/admin/inviteplayer", "admin"),
    ("GET", "/admin/getplayerroles", "admin"),
    ("POST", "/admin/updateroles", "admin"),
    ("POST", "/admin/addplayer", "admin"),
    ("GET", "/admin/dropplayer", "admin"),
    ("GET", "/admin/reactivateplayer", "admin"),
]


def _request(client: TestClient, method: str, path: str, headers: dict[str, str] | None = None):
    # Required query for getplayerroles so auth gating is exercised (not 422).
    get_kwargs: dict[str, object] = {}
    if path == "/admin/getplayerroles":
        get_kwargs["params"] = {"email": "roles@example.com"}
    if method == "GET":
        return client.get(path, headers=headers, **get_kwargs)
    return client.post(path, json={}, headers=headers)


def test_public_noop_routes(client: TestClient) -> None:
    for method, path in PUBLIC_NOOP:
        response = _request(client, method, path)
        assert response.status_code == 200, f"{method} {path} -> {response.status_code}"
        if path != "/health":
            assert response.json() == {"noop": True}, path


def test_protected_noop_routes_require_auth(client: TestClient) -> None:
    for method, path, _role in PROTECTED_NOOP + PROTECTED_IMPLEMENTED:
        response = _request(client, method, path)
        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"


def test_protected_noop_routes_with_role(client: TestClient) -> None:
    for method, path, role in PROTECTED_NOOP:
        token, _ = make_access_token(roles=[role])
        response = _request(client, method, path, headers=auth_header(token))
        assert response.status_code == 200, f"{method} {path} -> {response.status_code}"
        assert response.json() == {"noop": True}, path


def test_openapi_lists_expected_paths(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    implemented = [(m, p) for m, p, _ in PROTECTED_NOOP + PROTECTED_IMPLEMENTED]
    for method, path in PUBLIC_NOOP + IMPLEMENTED_PUBLIC + implemented:
        assert path in paths, f"missing {path}"
        assert method.lower() in paths[path], f"missing {method} {path}"
