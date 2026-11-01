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
    ("POST", "/user/updatedetails", "DETAILS"),
    ("GET", "/admin/listplayers", "ADMIN"),
    ("GET", "/admin/historicrounds", "ADMIN"),
    ("GET", "/admin/listpredictions", "ADMIN"),
    ("GET", "/admin/listpredictionsnew", "ADMIN"),
    ("POST", "/admin/addprediction", "ADMIN"),
    ("POST", "/admin/addplayer", "ADMIN"),
    ("GET", "/admin/getround", "ADMIN"),
    ("GET", "/admin/listmatchesnew", "ADMIN"),
    ("GET", "/admin/getmatch", "ADMIN"),
    ("POST", "/admin/addmatches", "ADMIN"),
    ("GET", "/admin/dropmatch", "ADMIN"),
    ("POST", "/admin/addpredictions", "ADMIN"),
    ("GET", "/admin/dropprediction", "ADMIN"),
    ("POST", "/admin/setspecialmessage", "ADMIN"),
    ("GET", "/admin/getspecialmessage", "ADMIN"),
    ("GET", "/admin/endspecialmessage", "ADMIN"),
    ("GET", "/admin/dropplayer", "ADMIN"),
    ("GET", "/admin/reactivateplayer", "ADMIN"),
    ("GET", "/euros/getprediction", "EUROWIZARD"),
    ("POST", "/euros/addprediction", "EUROWIZARD"),
    ("GET", "/worldcup/getprediction", "QATARHERO"),
    ("POST", "/worldcup/addprediction", "QATARHERO"),
]

PROTECTED_IMPLEMENTED = [
    ("GET", "/user/getprediction", "PLAYER"),
    ("POST", "/user/addprediction", "PLAYER"),
    ("GET", "/admin/listleagues", "ADMIN"),
    ("GET", "/admin/listrounds", "ADMIN"),
    ("GET", "/admin/currentround", "ADMIN"),
    ("POST", "/admin/addround", "ADMIN"),
    ("POST", "/admin/endround", "ADMIN"),
    ("GET", "/admin/listmatches", "ADMIN"),
    ("POST", "/admin/addmatch", "ADMIN"),
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
