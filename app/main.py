"""Aplicação Release Gate: decide SUCCESS ou FAILURE e registra execuções."""

from __future__ import annotations

import html
import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import __version__

SERVICE_NAME = "python-devops-pipeline"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000
MAX_BODY_BYTES = 65_536
DASHBOARD_TEMPLATE = Path(__file__).resolve().parent.parent / "index.html"

_LEDGER_LOCK = threading.Lock()

SIGNAL_CARDS = (
    (
        "dependencies_installed",
        "Dependências",
        "DEPENDENCIES_INSTALLED",
        "O ambiente Python recebeu as bibliotecas do requirements.txt.",
    ),
    (
        "tests_passed",
        "Testes",
        "TESTS_PASSED",
        "O Pytest aprovou o release gate.",
    ),
    (
        "image_built",
        "Imagem Docker",
        "IMAGE_BUILT",
        "A imagem da aplicação foi construída.",
    ),
    (
        "container_running",
        "Container",
        "CONTAINER_RUNNING",
        "O container executou a decisão do gate.",
    ),
)


@dataclass(frozen=True)
class ReleaseSignals:
    dependencies_installed: bool
    tests_passed: bool
    image_built: bool
    container_running: bool

    def failed_checks(self) -> list[str]:
        names = (
            "dependencies_installed",
            "tests_passed",
            "image_built",
            "container_running",
        )
        return [name for name in names if not getattr(self, name)]


def evaluate_release(signals: ReleaseSignals) -> str:
    if signals.failed_checks():
        return "FAILURE"
    return "SUCCESS"


def build_report(signals: ReleaseSignals) -> dict:
    return {
        "service": SERVICE_NAME,
        "version": __version__,
        "status": evaluate_release(signals),
        "checks": asdict(signals),
        "failed_checks": signals.failed_checks(),
    }


def _as_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "success"}


def signals_from_env(environ: dict[str, str] | None = None) -> ReleaseSignals:
    env = os.environ if environ is None else environ
    return ReleaseSignals(
        dependencies_installed=_as_bool(env.get("DEPENDENCIES_INSTALLED")),
        tests_passed=_as_bool(env.get("TESTS_PASSED")),
        image_built=_as_bool(env.get("IMAGE_BUILT")),
        container_running=_as_bool(env.get("CONTAINER_RUNNING")),
    )


def ledger_path() -> Path:
    return Path(os.environ.get("LEDGER_PATH", "data/runs.json"))


def load_runs(path: Path | None = None) -> list[dict]:
    target = path or ledger_path()
    if not target.exists():
        return []
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("o ledger precisa ser uma lista")
    return data


def save_runs(runs: list[dict], path: Path | None = None) -> None:
    target = path or ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(runs, indent=2, ensure_ascii=False) + "\n"
    target.write_text(payload, encoding="utf-8")


def add_run(
    status: str,
    summary: str,
    stage: str = "manual",
    path: Path | None = None,
) -> dict:
    normalized = status.strip().upper()
    if normalized not in {"SUCCESS", "FAILURE"}:
        raise ValueError("status precisa ser SUCCESS ou FAILURE")
    clean_summary = " ".join(summary.split())
    if not clean_summary:
        raise ValueError("summary é obrigatório")
    clean_stage = " ".join(stage.split()) or "manual"
    with _LEDGER_LOCK:
        runs = load_runs(path)
        record = {
            "id": max((int(item["id"]) for item in runs), default=0) + 1,
            "status": normalized,
            "stage": clean_stage,
            "summary": clean_summary,
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        runs.append(record)
        save_runs(runs, path)
    return record


def delete_run(run_id: int, path: Path | None = None) -> bool:
    with _LEDGER_LOCK:
        runs = load_runs(path)
        kept = [item for item in runs if int(item["id"]) != run_id]
        if len(kept) == len(runs):
            return False
        save_runs(kept, path)
    return True


def render_dashboard(runs: list[dict], notice: str = "", report: dict | None = None) -> str:
    notices = {
        "registrado": "Execução registrada no ledger.",
        "removido": "Execução removida do ledger.",
        "ausente": "Execução não encontrada.",
    }
    notice_html = ""
    if notice in notices:
        notice_html = f'<p class="notice">{notices[notice]}</p>'

    gate = report or build_report(signals_from_env())
    failed = gate.get("failed_checks") or []
    if failed:
        summary = "Falhou: " + ", ".join(str(item) for item in failed)
    else:
        summary = "Todos os sinais passaram."

    if runs:
        rows = "\n".join(_render_row(run) for run in reversed(runs))
    else:
        rows = '<tr><td colspan="6">Nenhuma execução registrada.</td></tr>'

    success_count = sum(1 for run in runs if run.get("status") == "SUCCESS")
    failure_count = sum(1 for run in runs if run.get("status") == "FAILURE")
    template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "__VERSION__": html.escape(__version__),
        "__NOTICE__": notice_html,
        "__GATE_STATUS__": html.escape(str(gate.get("status", "UNKNOWN"))),
        "__GATE_CLASS__": html.escape(str(gate.get("status", ""))),
        "__GATE_SUMMARY__": html.escape(summary),
        "__SIGNAL_CARDS__": _render_signal_cards(gate.get("checks") or {}),
        "__TOTAL_COUNT__": str(len(runs)),
        "__SUCCESS_COUNT__": str(success_count),
        "__FAILURE_COUNT__": str(failure_count),
        "__ROWS__": rows,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def _render_signal_cards(checks: dict) -> str:
    cards = []
    for key, title, env_name, hint in SIGNAL_CARDS:
        passed = bool(checks.get(key, False))
        state = "ok" if passed else "bad"
        label = "PASS" if passed else "FAIL"
        cards.append(
            "<div class=\"signal "
            f'{state}">'
            f"<span class=\"env\">{html.escape(env_name)}</span>"
            f"<b>{html.escape(title)}</b>"
            f"<strong class=\"{'SUCCESS' if passed else 'FAILURE'}\">{label}</strong>"
            f"<small>{html.escape(hint)}</small>"
            "</div>"
        )
    return "\n".join(cards)


def _format_when(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    if "T" in text:
        return text.replace("T", " ")[:16]
    return text


def _render_row(run: dict) -> str:
    status = str(run["status"])
    run_id = int(run["id"])
    when = html.escape(_format_when(run.get("created_at")))
    return (
        "<tr>"
        f"<td>{run_id}</td>"
        f'<td class="{html.escape(status)}">{html.escape(status)}</td>'
        f"<td>{html.escape(str(run['stage']))}</td>"
        f"<td>{html.escape(str(run['summary']))}</td>"
        f"<td>{when}</td>"
        "<td>"
        f'<form class="inline" method="post" action="/api/runs/{run_id}/delete">'
        '<button type="submit">Remover</button>'
        "</form>"
        "</td>"
        "</tr>"
    )


class RequestError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def create_server(host: str, port: int, ledger: Path | None = None) -> ThreadingHTTPServer:
    target = ledger or ledger_path()
    handler = _handler_factory(target)
    return ThreadingHTTPServer((host, port), handler)


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    server = create_server(host, port)
    print(f"listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _handler_factory(ledger: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                notice = parse_qs(parsed.query).get("notice", [""])[0]
                body = render_dashboard(
                    load_runs(ledger),
                    notice,
                    build_report(signals_from_env()),
                ).encode("utf-8")
                self._send(200, "text/html; charset=utf-8", body)
                return
            if parsed.path == "/api/health":
                self._send_json(200, build_report(signals_from_env()))
                return
            if parsed.path == "/api/runs":
                self._send_json(200, {"runs": load_runs(ledger)})
                return
            if parsed.path == "/favicon.ico":
                self._send(204, "text/plain; charset=utf-8", b"")
                return
            self._send_json(404, {"error": "não encontrado"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/runs":
                    record = self._create_run()
                    if self._wants_html():
                        self._redirect("/?notice=registrado")
                        return
                    self._send_json(201, record)
                    return
                if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/delete"):
                    run_id = _run_id_from_path(parsed.path)
                    removed = delete_run(run_id, ledger)
                    if self._wants_html():
                        self._redirect("/?notice=removido" if removed else "/?notice=ausente")
                        return
                    if not removed:
                        self._send_json(404, {"error": "execução não encontrada"})
                        return
                    self._send_json(200, {"deleted": run_id})
                    return
                self._send_json(404, {"error": "não encontrado"})
            except RequestError as exc:
                self._send_json(exc.status, {"error": exc.message})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})

        def log_message(self, fmt: str, *args: object) -> None:
            print(f"[http] {self.address_string()} {fmt % args}", flush=True)

        def _create_run(self) -> dict:
            payload = self._read_payload()
            status = str(payload.get("status", ""))
            summary = str(payload.get("summary", ""))
            stage = str(payload.get("stage", "manual"))
            return add_run(status, summary, stage, ledger)

        def _read_payload(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY_BYTES:
                raise RequestError(413, "corpo da requisição excede o limite")
            raw = self.rfile.read(length)
            content_type = self.headers.get("Content-Type", "")
            if "application/json" in content_type:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise RequestError(400, "JSON inválido") from exc
                if not isinstance(data, dict):
                    raise RequestError(400, "JSON precisa ser um objeto")
                return {key: "" if value is None else str(value) for key, value in data.items()}
            form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
            return {key: values[-1] if values else "" for key, values in form.items()}

        def _wants_html(self) -> bool:
            content_type = self.headers.get("Content-Type", "")
            accept = self.headers.get("Accept", "")
            return "application/json" not in content_type and "text/html" in accept

        def _redirect(self, location: str) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, "application/json; charset=utf-8", body)

    return Handler


def _run_id_from_path(path: str) -> int:
    parts = [part for part in path.split("/") if part]
    # ["api", "runs", "{id}", "delete"]
    if len(parts) != 4:
        raise RequestError(404, "não encontrado")
    try:
        return int(parts[2])
    except ValueError as exc:
        raise RequestError(404, "não encontrado") from exc


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["serve"]:
        host = args[1] if len(args) > 1 else DEFAULT_HOST
        port = int(args[2]) if len(args) > 2 else int(os.environ.get("PORT", DEFAULT_PORT))
        serve(host, port)
        return 0

    report = build_report(signals_from_env())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
