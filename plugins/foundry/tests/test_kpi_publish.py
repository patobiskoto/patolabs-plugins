"""PLP-325: `routing kpi-publish` against a simulated loopback server; fake credentials only."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

import foundry
from foundry import kpi_publish
from foundry.trackers.base import Tracker
from foundry.trackers.devhub import DevHubTracker

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
FAKE = "fake-metrics-token-0123456789abcdef"


def _record(n, issue="DEVHUB-1"):
    return {"record_id": f"kpi-{n}", "kind": "attempt", "anchor": {"type": "issue", "command_id": None, "issue_id": issue}}


def _document(*sizes, issue="DEVHUB-1"):
    count = 0
    batches = []
    for size in sizes:
        batches.append({"schema_version": "devhub-observations.v2",
                        "records": [_record(count + i, issue) for i in range(size)]})
        count += size
    return {"export_schema": "foundry-kpi-export.v1", "batches": batches}


class Server:
    def __init__(self, behaviour):
        self.seen, self.stored = [], set()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.seen.append((self.path, self.headers.get("Authorization"), json.loads(body)))
                status, payload, headers = behaviour(outer, json.loads(body))
                raw = json.dumps(payload).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


CONTRACT = {"X-DevHub-Contract": "devhub-observations.v2"}


def _ok(server, batch):
    results = []
    for record in batch["records"]:
        if record["record_id"] == "kpi-1":
            results.append({"record_id": "kpi-1", "status": "error", "code": "anchor_conflict"})
        elif record["record_id"] in server.stored:
            results.append({"record_id": record["record_id"], "status": "replayed", "code": None})
        else:
            server.stored.add(record["record_id"])
            results.append({"record_id": record["record_id"], "status": "created", "code": None})
    return 200, {"schema_version": "devhub-observations.v2", "results": results}, CONTRACT


@pytest.fixture
def server():
    made = []

    def make(behaviour=_ok):
        made.append(Server(behaviour))
        return made[-1]

    yield make
    for item in made:
        item.close()


def test_publish_reports_created_replayed_and_refusals_and_republication_replays(server):
    srv = server()
    document = _document(3)
    first = kpi_publish.publish(document, "DEVHUB", url=srv.url, token=FAKE)
    assert first["totals"] == {"created": 2, "replayed": 0, "refused": 1, "unsent": 0}
    assert first["refusals"] == [{"record_id": "kpi-1", "code": "anchor_conflict"}]
    second = kpi_publish.publish(document, "DEVHUB", url=srv.url, token=FAKE)
    assert second["totals"] == {"created": 0, "replayed": 2, "refused": 1, "unsent": 0}
    path, auth, body = srv.seen[0]
    assert path == "/api/economics/v2/projects/DEVHUB/observations:batch"
    assert auth == f"Bearer {FAKE}" and body == document["batches"][0]
    assert FAKE not in json.dumps(first)


@pytest.mark.parametrize("behaviour,error", [
    (lambda s, b: (500, {"error": {"code": "economic_unavailable"}}, CONTRACT), "http_500_economic_unavailable"),
    (lambda s, b: (200, {"schema_version": "devhub-observations.v2", "results": []}, CONTRACT), "invalid_response"),
    (lambda s, b: (200, {"schema_version": "devhub-observations.v2", "results": []}, {}), "contract_mismatch"),
    (lambda s, b: (302, {}, {"Location": "http://127.0.0.1:1/x"}), "http_302_provider_error"),
])
def test_batch_failures_are_reported_and_next_batch_still_sent(server, behaviour, error):
    srv = server(behaviour)
    report = kpi_publish.publish(_document(1, 1), "DEVHUB", url=srv.url, token=FAKE)
    assert [b.get("error") for b in report["batches"]] == [error, error]
    assert report["totals"]["unsent"] == 2 and len(srv.seen) == 2


def test_unreachable_server_is_tracker_unavailable():
    report = kpi_publish.publish(_document(1), "DEVHUB", url="http://127.0.0.1:9", token=FAKE, timeout=1)
    assert report["batches"][0]["error"] == "tracker_unavailable"


def test_document_of_another_project_is_refused_before_any_request(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps(_document(1, issue="OTHER-1")))
    with pytest.raises(kpi_publish.PublishError, match="anchored outside project DEVHUB"):
        kpi_publish.load_document(str(path), "DEVHUB")
    path.write_text(json.dumps({"export_schema": "x", "batches": []}))
    with pytest.raises(kpi_publish.PublishError):
        kpi_publish.load_document(str(path), "DEVHUB")
    with pytest.raises(kpi_publish.PublishError):
        kpi_publish.load_document(str(path), "DEV/HUB")


def _env(monkeypatch, srv, tracker="devhub"):
    monkeypatch.setattr(foundry, "_effective_tracker_selection", lambda cwd=None: (tracker, None))
    monkeypatch.setenv("DEVHUB_METRICS_TOKEN", FAKE)
    monkeypatch.setenv("DEVHUB_URL", srv.url)


def test_run_exit_status_and_report(server, monkeypatch, tmp_path, capsys):
    srv = server()
    _env(monkeypatch, srv)
    path = tmp_path / "export.json"
    path.write_text(json.dumps(_document(1)))
    assert kpi_publish.run(str(path), "DEVHUB") == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["totals"]["created"] == 1 and FAKE not in out.out + out.err
    path.write_text(json.dumps(_document(3)))
    assert kpi_publish.run(str(path), "DEVHUB") == 1


def test_run_refuses_without_write_for_an_adapter_without_the_capability(server, monkeypatch, tmp_path, capsys):
    srv = server()
    path = tmp_path / "export.json"
    path.write_text(json.dumps(_document(1)))
    for name in ("linear", "ghprojects", "youtrack", "unknown"):
        _env(monkeypatch, srv, name)
        assert kpi_publish.run(str(path), "DEVHUB") == 2
    assert srv.seen == [] and "does not declare" in capsys.readouterr().err


def test_run_refuses_without_token_or_with_a_remote_http_url(server, monkeypatch, tmp_path, capsys):
    srv = server()
    _env(monkeypatch, srv)
    path = tmp_path / "export.json"
    path.write_text(json.dumps(_document(1)))
    assert kpi_publish.run(str(path), "DEVHUB", url="http://example.invalid") == 2
    monkeypatch.setenv("FOUNDRY_RUNTIME_CONFIG_ISOLATED", "1")
    assert kpi_publish.run(str(path), "DEVHUB") == 2
    assert FAKE not in capsys.readouterr().err and srv.seen == []


def test_capability_is_declared_only_by_the_devhub_adapter():
    assert Tracker.kpi_publication_supported is False
    assert DevHubTracker.kpi_publication_supported is True
    from foundry.trackers.ghprojects import GitHubProjectsTracker
    from foundry.trackers.linear import LinearTracker
    from foundry.trackers.youtrack import YouTrackTracker
    assert not any(c.kpi_publication_supported for c in (GitHubProjectsTracker, LinearTracker, YouTrackTracker))


def test_metrics_token_is_a_keychain_secret_distinct_from_the_tracker_token():
    from foundry import config
    assert "DEVHUB_METRICS_TOKEN" in config._KEYCHAIN_SECRETS and kpi_publish.TOKEN_KEY != "DEVHUB_TRACKER_TOKEN"
    assert config._secret_setting("DEVHUB_METRICS_TOKEN")


def test_only_the_cli_verb_imports_the_publisher_and_the_reader_stays_offline():
    tooling = PLUGIN_ROOT / "tooling"
    importers = {p.name for p in tooling.rglob("*.py")
                 if re.search(r"^\s*(from foundry import .*\bkpi_publish\b|from foundry\.kpi_publish\b)",
                              p.read_text(), re.M)}
    assert importers == {"routing.py"}
    reader = (tooling / "foundry" / "kpi_export.py").read_text()
    assert "kpi_publish" not in reader and "urllib" not in reader
