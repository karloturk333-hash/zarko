"""HTTP sloj dashboarda — stdlib BaseHTTPRequestHandler, read-only.

HTML crta web.render. Agregaciju (assemble) zove iz view.py po zahtjevu.
CSS je u web/static/; URL i dalje /static/...
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import rules
import view_history
from position import PortfolioView, ViewGreska
from rules import PravilaGreska
from web.render import render_html

DEFAULT_HISTORY = view_history.DEFAULT_DB
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8787
LOOPBACK = {"127.0.0.1", "::1", "localhost"}

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "pico.min.css": "text/css; charset=utf-8",
    "dashboard.css": "text/css; charset=utf-8",
}


# ── HTTP ─────────────────────────────────────────────────────────────────────

def static_response(url_path: str) -> tuple[int, bytes, str] | None:
    """Serviraj samo allowlist iz web/static/. None = nije /static/ zahtjev."""
    if not url_path.startswith("/static/"):
        return None
    name = url_path[len("/static/"):]
    if name not in STATIC_FILES:
        return 404, b"nema", "text/plain; charset=utf-8"
    target = (STATIC_DIR / name).resolve()
    if target.parent != STATIC_DIR.resolve() or not target.is_file():
        return 404, b"nema", "text/plain; charset=utf-8"
    return 200, target.read_bytes(), STATIC_FILES[name]


def make_handler(db_path: Path, state_dir: Path, rules_path: Path,
                 history_path: Path | None = None):
    history_path = Path(history_path) if history_path else DEFAULT_HISTORY

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            # Read-only: nikad --snapshot, nikad pisanje u *.db / rules.yaml / .env.
            parsed = urlparse(self.path)
            static = static_response(parsed.path)
            if static is not None:
                code, body, ctype = static
                self._send(code, body, ctype)
                return
            if parsed.path not in ("/", "/json", "/health"):
                self._send(404, b"nema", "text/plain; charset=utf-8")
                return
            if parsed.path == "/health":
                self._send(200, b"ok\n", "text/plain; charset=utf-8")
                return

            qs = parse_qs(parsed.query)
            category = (qs.get("category") or [None])[0] or None
            source = (qs.get("source") or [None])[0] or None
            currency = (qs.get("currency") or [None])[0] or None
            history = view_history.load_chart_history(db_path, history_path)

            try:
                # Uvoz unutra: view uvozi ovaj modul, pa bi uvoz na razini zatvorio krug.
                import view as view_mod

                pravila = rules.ucitaj_pravila(rules_path)
                view = view_mod.assemble(db_path, state_dir, pravila)
            except (ViewGreska, PravilaGreska) as err:
                if parsed.path == "/json":
                    payload = json.dumps({"error": str(err)}, ensure_ascii=False).encode()
                    self._send(500, payload, "application/json; charset=utf-8")
                    return
                page = render_html(
                    PortfolioView(
                        as_of=None, total_value_eur=0, positions_value_eur=0,
                        cash_eur=0, total_cost_eur=None, total_pnl_eur=None,
                        sources=[], positions=[],
                    ),
                    error=str(err),
                    history=history,
                )
                self._send(500, page.encode("utf-8"), "text/html; charset=utf-8")
                return

            if parsed.path == "/json":
                payload = json.dumps(view.to_dict(), ensure_ascii=False, indent=2).encode()
                self._send(200, payload, "application/json; charset=utf-8")
                return

            page = render_html(
                view, category=category, source=source, currency=currency,
                history=history,
            )
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            self._send(405, b"read-only\n", "text/plain; charset=utf-8")

    return Handler


def public_bind_warning(bind: str) -> str | None:
    if bind in LOOPBACK:
        return None
    return (
        f"UPOZORENJE: bind={bind} nije localhost. Hetzner firewall mora "
        f"ostati samo SSH; javni pristup ide kroz Cloudflare Tunnel ili "
        f"Tailscale, ne kroz 0.0.0.0."
    )


def serve(bind: str, port: int, db_path: Path, state_dir: Path,
          rules_path: Path, history_path: Path | None = None) -> None:
    warning = public_bind_warning(bind)
    if warning:
        print(warning, file=sys.stderr)
    httpd = ThreadingHTTPServer(
        (bind, port),
        make_handler(db_path, state_dir, rules_path, history_path),
    )
    print(f"Dashboard na http://{bind}:{port}  (Ctrl-C za prekid)", file=sys.stderr)
    print(f"JSON: http://{bind}:{port}/json", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nzaustavljeno", file=sys.stderr)
        httpd.server_close()

