import json
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from app.main import (
    ReleaseSignals,
    add_run,
    build_report,
    create_server,
    delete_run,
    evaluate_release,
    main,
    render_dashboard,
)

SIGNAL_NAMES = (
    "dependencies_installed",
    "tests_passed",
    "image_built",
    "container_running",
)


def _signals(**overrides: bool) -> ReleaseSignals:
    values = {name: True for name in SIGNAL_NAMES}
    values.update(overrides)
    return ReleaseSignals(**values)


def test_release_succeeds_when_every_check_passes():
    report = build_report(_signals())

    assert evaluate_release(_signals()) == "SUCCESS"
    assert report["status"] == "SUCCESS"
    assert report["service"] == "python-devops-pipeline"
    assert report["failed_checks"] == []


@pytest.mark.parametrize("failed_check", SIGNAL_NAMES)
def test_release_fails_when_any_check_fails(failed_check):
    signals = _signals(**{failed_check: False})

    assert evaluate_release(signals) == "FAILURE"
    assert build_report(signals)["failed_checks"] == [failed_check]


def test_ledger_records_and_removes_runs(tmp_path):
    ledger = tmp_path / "runs.json"

    created = add_run(" success ", "  container executado  ", " container ", ledger)

    assert created["id"] == 1
    assert created["status"] == "SUCCESS"
    assert created["stage"] == "container"
    assert created["summary"] == "container executado"
    assert delete_run(created["id"], ledger) is True
    assert delete_run(created["id"], ledger) is False


def test_ledger_rejects_unknown_status(tmp_path):
    with pytest.raises(ValueError, match="SUCCESS ou FAILURE"):
        add_run("MAYBE", "resultado ambíguo", path=tmp_path / "runs.json")


def test_dashboard_escapes_summary_text():
    page = render_dashboard(
        [
            {
                "id": 3,
                "status": "FAILURE",
                "stage": "pytest",
                "summary": "<script>alert(1)</script>",
            }
        ]
    )

    assert "&lt;script&gt;" in page
    assert "<script>alert" not in page
    assert "Nenhuma execução registrada." not in page


def test_dashboard_explains_pipeline_and_gate():
    page = render_dashboard([], report=build_report(_signals()))

    assert "Caminho da pipeline" in page
    assert "Checkout" in page
    assert "DEPENDENCIES_INSTALLED" in page
    assert "Release gate agora" in page
    assert 'class="badge SUCCESS"' in page
    assert "Jenkinsfile" in page


def test_main_prints_success_and_returns_zero(monkeypatch, capsys):
    for name in (
        "DEPENDENCIES_INSTALLED",
        "TESTS_PASSED",
        "IMAGE_BUILT",
        "CONTAINER_RUNNING",
    ):
        monkeypatch.delenv(name, raising=False)

    assert main([]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "SUCCESS"


def test_main_returns_failure_exit_code(monkeypatch, capsys):
    monkeypatch.setenv("TESTS_PASSED", "false")

    assert main([]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAILURE"
    assert payload["failed_checks"] == ["tests_passed"]


@pytest.fixture
def client(tmp_path):
    server = create_server("127.0.0.1", 0, tmp_path / "runs.json")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()


def _request(method, url, data=None, headers=None):
    body = None if data is None else urlencode(data).encode("utf-8")
    request = Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urlopen(request) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8")


def test_http_ledger_creates_lists_and_deletes(client):
    status, _, body = _request(
        "POST",
        f"{client}/api/runs",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        data=None,
    )
    assert status == 400

    raw = json.dumps(
        {"status": "FAILURE", "stage": "pytest", "summary": "teste reprovado de propósito"}
    ).encode("utf-8")
    request = Request(
        f"{client}/api/runs",
        data=raw,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(request) as response:
        created = json.loads(response.read().decode("utf-8"))
        assert response.status == 201
    assert created["status"] == "FAILURE"

    status, _, body = _request("GET", f"{client}/api/runs")
    assert status == 200
    assert json.loads(body)["runs"][0]["summary"] == "teste reprovado de propósito"

    page_status, _, page = _request("GET", f"{client}/")
    assert page_status == 200
    assert "teste reprovado de propósito" in page

    status, _, body = _request(
        "POST",
        f"{client}/api/runs/{created['id']}/delete",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    assert status == 200
    assert json.loads(body)["deleted"] == created["id"]


def test_health_endpoint_reports_release_gate(client):
    status, _, body = _request("GET", f"{client}/api/health")

    assert status == 200
    assert json.loads(body)["status"] in {"SUCCESS", "FAILURE"}
