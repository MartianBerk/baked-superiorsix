from fastapi.testclient import TestClient
from tests.baked.superiorsix.tokens import auth_header, make_access_token

# Legacy supersix routes mapped to path prefixes (subdomains → /prefix).
PUBLIC = [
    ("GET", "/health"),
    ("GET", "/game/meta"),
    ("GET", "/game/livematches"),
    ("GET", "/game/livescores"),
    ("GET", "/game/currentround"),
    ("GET", "/game/specialmessage"),
    ("GET", "/game/matchdetail"),
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

PROTECTED = [
    ("GET", "/user/getprediction", "PLAYER"),
    ("POST", "/user/addprediction", "PLAYER"),
    ("POST", "/user/updatedetails", "DETAILS"),
    ("GET", "/admin/listleagues", "ADMIN"),
    ("GET", "/admin/listplayers", "ADMIN"),
    ("GET", "/admin/listrounds", "ADMIN"),
    ("GET", "/admin/currentround", "ADMIN"),
    ("GET", "/admin/historicrounds", "ADMIN"),
    ("GET", "/admin/listpredictions", "ADMIN"),
    ("GET", "/admin/listpredictionsnew", "ADMIN"),
    ("POST", "/admin/addprediction", "ADMIN"),
    ("POST", "/admin/addplayer", "ADMIN"),
    ("POST", "/admin/addround", "ADMIN"),
    ("POST", "/admin/endround", "ADMIN"),
    ("GET", "/admin/getround", "ADMIN"),
    ("GET", "/admin/listmatches", "ADMIN"),
    ("GET", "/admin/listmatchesnew", "ADMIN"),
    ("GET", "/admin/getmatch", "ADMIN"),
    ("POST", "/admin/addmatch", "ADMIN"),
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


def _request(client: TestClient, method: str, path: str, headers: dict[str, str] | None = None):
    if method == "GET":
        return client.get(path, headers=headers)
    return client.post(path, json={}, headers=headers)


def test_public_routes_are_noop(client: TestClient) -> None:
    for method, path in PUBLIC:
        response = _request(client, method, path)
        assert response.status_code == 200, f"{method} {path} -> {response.status_code}"
        if path != "/health":
            assert response.json() == {"noop": True}, path


def test_protected_routes_require_auth(client: TestClient) -> None:
    for method, path, _role in PROTECTED:
        response = _request(client, method, path)
        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"


def test_protected_routes_reject_wrong_role(client: TestClient) -> None:
    token, _ = make_access_token(roles=["user"])
    headers = auth_header(token)
    for method, path, _role in PROTECTED:
        response = _request(client, method, path, headers=headers)
        assert response.status_code == 403, f"{method} {path} -> {response.status_code}"


def test_protected_routes_are_noop_with_role(client: TestClient) -> None:
    for method, path, role in PROTECTED:
        token, _ = make_access_token(roles=[role])
        response = _request(client, method, path, headers=auth_header(token))
        assert response.status_code == 200, f"{method} {path} -> {response.status_code}"
        assert response.json() == {"noop": True}, path


def test_openapi_lists_expected_paths(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for method, path in PUBLIC:
        assert path in paths, f"missing {path}"
        assert method.lower() in paths[path], f"missing {method} {path}"
    for method, path, _role in PROTECTED:
        assert path in paths, f"missing {path}"
        assert method.lower() in paths[path], f"missing {method} {path}"
