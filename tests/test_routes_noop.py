from fastapi.testclient import TestClient

from baked_superiorsix.main import app

client = TestClient(app)

# Legacy supersix routes mapped to path prefixes (subdomains → /prefix).
# Role-gated routes are still callable while JWT enforcement is a stub.
EXPECTED = [
    ("GET", "/health"),
    ("GET", "/game/meta"),
    ("GET", "/game/livematches"),
    ("GET", "/game/livescores"),
    ("GET", "/game/currentround"),
    ("GET", "/game/specialmessage"),
    ("GET", "/game/matchdetail"),
    ("GET", "/user/getprediction"),
    ("POST", "/user/addprediction"),
    ("POST", "/user/updatedetails"),
    ("GET", "/stats/aggregate"),
    ("GET", "/stats/winners"),
    ("GET", "/admin/listleagues"),
    ("GET", "/admin/listplayers"),
    ("GET", "/admin/listrounds"),
    ("GET", "/admin/currentround"),
    ("GET", "/admin/historicrounds"),
    ("GET", "/admin/listpredictions"),
    ("GET", "/admin/listpredictionsnew"),
    ("POST", "/admin/addprediction"),
    ("POST", "/admin/addplayer"),
    ("POST", "/admin/addround"),
    ("POST", "/admin/endround"),
    ("GET", "/admin/getround"),
    ("GET", "/admin/listmatches"),
    ("GET", "/admin/listmatchesnew"),
    ("GET", "/admin/getmatch"),
    ("POST", "/admin/addmatch"),
    ("POST", "/admin/addmatches"),
    ("GET", "/admin/dropmatch"),
    ("POST", "/admin/addpredictions"),
    ("GET", "/admin/dropprediction"),
    ("POST", "/admin/setspecialmessage"),
    ("GET", "/admin/getspecialmessage"),
    ("GET", "/admin/endspecialmessage"),
    ("GET", "/admin/dropplayer"),
    ("GET", "/admin/reactivateplayer"),
    ("GET", "/euros/matches"),
    ("GET", "/euros/scores"),
    ("GET", "/euros/listpredictions"),
    ("GET", "/eurowizard/listpredictions"),
    ("GET", "/euros/getpredictions"),
    ("GET", "/euros/getprediction"),
    ("POST", "/euros/addprediction"),
    ("GET", "/worldcup/matches"),
    ("GET", "/worldcup/scores"),
    ("GET", "/worldcup/listpredictions"),
    ("GET", "/qatarhero/listpredictions"),
    ("GET", "/worldcup/getpredictions"),
    ("GET", "/worldcup/getprediction"),
    ("POST", "/worldcup/addprediction"),
]


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_all_legacy_routes_are_noop() -> None:
    for method, path in EXPECTED:
        if method == "GET":
            response = client.get(path)
        else:
            response = client.post(path, json={})
        assert response.status_code == 200, f"{method} {path} -> {response.status_code}"
        if path != "/health":
            assert response.json() == {"noop": True}, path


def test_openapi_lists_expected_paths() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for method, path in EXPECTED:
        assert path in paths, f"missing {path}"
        assert method.lower() in paths[path], f"missing {method} {path}"
