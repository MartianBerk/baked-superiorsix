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
]

PROTECTED_NOOP = [
    ("GET", "/admin/historicrounds", "game-master"),
    ("GET", "/admin/listpredictions", "game-master"),
    ("GET", "/admin/listpredictionsnew", "game-master"),
    ("POST", "/admin/addprediction", "game-master"),
    ("GET", "/admin/getround", "game-master"),
    ("GET", "/admin/listmatchesnew", "game-master"),
    ("GET", "/admin/getmatch", "game-master"),
    ("POST", "/admin/addmatches", "game-master"),
    ("GET", "/admin/dropmatch", "game-master"),
    ("POST", "/admin/addpredictions", "game-master"),
    ("GET", "/admin/dropprediction", "game-master"),
    ("POST", "/admin/setspecialmessage", "game-master"),
    ("GET", "/admin/getspecialmessage", "game-master"),
    ("GET", "/admin/endspecialmessage", "game-master"),
    ("GET", "/euros/getprediction", "euros"),
    ("POST", "/euros/addprediction", "euros"),
    ("GET", "/worldcup/getprediction", "worldcup"),
    ("POST", "/worldcup/addprediction", "worldcup"),
]

PROTECTED_IMPLEMENTED = [
    ("GET", "/user/getprediction", "player"),
    ("POST", "/user/addprediction", "player"),
    ("POST", "/user/updatedetails", "user-profile"),
    ("GET", "/admin/listleagues", "game-master"),
    ("GET", "/admin/listrounds", "game-master"),
    ("GET", "/admin/currentround", "game-master"),
    ("POST", "/admin/addround", "game-master"),
    ("POST", "/admin/endround", "game-master"),
    ("GET", "/admin/listmatches", "game-master"),
    ("POST", "/admin/addmatch", "game-master"),
    ("GET", "/admin/listplayers", "admin"),
    ("POST", "/admin/addplayer", "admin"),
    ("GET", "/admin/dropplayer", "admin"),
    ("GET", "/admin/reactivateplayer", "admin"),
]


def _request(client: TestClient, method: str, path: str, headers: dict[str, str] | None = None):
    if method == "GET":
        return client.get(path, headers=headers)
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
