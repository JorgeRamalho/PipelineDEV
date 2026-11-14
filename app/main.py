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

_LEDGER_LOCK = threading.Lock()


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


def render_dashboard(runs: list[dict], notice: str = "") -> str:
    notices = {
        "registrado": "Execução registrada no ledger.",
        "removido": "Execução removida do ledger.",
        "ausente": "Execução não encontrada.",
    }
    notice_html = ""
    if notice in notices:
        notice_html = f'<p class="notice">{notices[notice]}</p>'

    if runs:
        rows = "\n".join(_render_row(run) for run in reversed(runs))
    else:
        rows = '<tr><td colspan="5">Nenhuma execução registrada.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PYTHON-DEVOPS-PIPELINE</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1c1915;
      --muted: #5e584f;
      --line: #ddd6c8;
      --paper: #f7f4ee;
      --card: #fffdf9;
      --success: #1f6b45;
      --failure: #9d2c2c;
    }}
    body {{
      margin: 0;
      font: 16px/1.45 Georgia, "Iowan Old Style", serif;
      background: var(--paper);
      color: var(--ink);
    }}
    main {{
      max-width: 920px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    h1 {{ font-size: 1.7rem; margin: 0 0 4px; }}
    .lede {{ color: var(--muted); margin: 0 0 24px; }}
    form, table {{
      background: var(--card);
      border: 1px solid var(--line);
      width: 100%;
    }}
    form {{ padding: 16px; margin-bottom: 20px; }}
    label {{ display: block; font-size: 0.85rem; margin: 10px 0 4px; }}
    input, select, button {{
      font: inherit;
      padding: 8px 10px;
      border: 1px solid var(--line);
      background: white;
    }}
    button {{ cursor: pointer; background: var(--ink); color: white; }}
    table {{ border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    .SUCCESS {{ color: var(--success); font-weight: 700; }}
    .FAILURE {{ color: var(--failure); font-weight: 700; }}
    .notice {{ padding: 10px 12px; background: #efe7d6; }}
    .inline {{ display: inline; }}
  </style>
</head>
<body>
  <main>
    <h1>PYTHON-DEVOPS-PIPELINE</h1>
    <p class="lede">Ledger de execuções do release gate. Versão {html.escape(__version__)}.</p>
    {notice_html}
    <form method="post" action="/api/runs">
      <label for="status">Resultado</label>
      <select id="status" name="status">
        <option value="SUCCESS">SUCCESS</option>
        <option value="FAILURE">FAILURE</option>
      </select>
      <label for="stage">Etapa</label>
      <input id="stage" name="stage" value="pipeline" required>
      <label for="summary">Resumo</label>
      <input id="summary" name="summary" required placeholder="O que esta execução comprovou">
      <button type="submit">Registrar execução</button>
    </form>
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Resultado</th>
          <th>Etapa</th>
          <th>Resumo</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
  </main>
</body>
</html>
"""


def _render_row(run: dict) -> str:
    status = str(run["status"])
    run_id = int(run["id"])
    return (
        "<tr>"
        f"<td>{run_id}</td>"
        f'<td class="{html.escape(status)}">{html.escape(status)}</td>'
        f"<td>{html.escape(str(run['stage']))}</td>"
        f"<td>{html.escape(str(run['summary']))}</td>"
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
                body = render_dashboard(load_runs(ledger), notice).encode("utf-8")
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
