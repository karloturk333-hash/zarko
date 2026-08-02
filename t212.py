"""Tanki read-only klijent za Trading212 public API.

Bez vanjskih dependencyja — čisti stdlib. Kredencijali se čitaju iz okoline
ili .env fajla u istom folderu. T212_ENV = live | demo, default live.

Autentikacija (dvije sheme, prema službenom OpenAPI specu):
  - authWithSecretKey  — HTTP Basic: API key kao username, API secret kao password.
    Ovo koriste novi ključevi (par key + secret). Postavi T212_API_KEY i T212_API_SECRET.
  - legacyApiKeyHeader — goli ključ u Authorization headeru, bez prefiksa.
    Stari jednodijelni ključevi. Postavi samo T212_API_KEY.

Klijent bira shemu sam: ako postoji secret => Basic, inače legacy.

Spec: https://docs.trading212.com/_bundle/api.yaml
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URLS = {
    "live": "https://live.trading212.com",
    "demo": "https://demo.trading212.com",
}

MAX_RETRIES = 4


class T212Error(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


def load_env(path: str | Path = ".env") -> None:
    """Učita KEY=VALUE parove iz .env u os.environ (postojeće varijable imaju prednost)."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class T212Client:
    def __init__(self, api_key: str | None = None, api_secret: str | None = None,
                 env: str | None = None):
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("T212_API_KEY", "")).strip()
        self.api_secret = (api_secret if api_secret is not None
                           else os.environ.get("T212_API_SECRET", "")).strip()
        if not self.api_key:
            raise SystemExit(
                "Nedostaje T212_API_KEY. Postavi ga u okolinu ili u .env (vidi .env.example).\n"
                "Ključ generiraj u Trading212: Settings → API — read-only dozvole + IP restrikcija.\n"
                "Ako si dobio DVA stringa (API key i secret key), oba idu u .env: "
                "T212_API_KEY i T212_API_SECRET."
            )

        if self.api_secret:
            token = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
            self.auth_header = f"Basic {token}"
            self.auth_scheme = "basic (key + secret)"
        else:
            # Legacy shema: goli ključ, bez prefiksa. Vrijedi samo za stare jednodijelne ključeve.
            self.auth_header = self.api_key
            self.auth_scheme = "legacy (goli ključ)"

        env = env or os.environ.get("T212_ENV", "live")
        if env not in BASE_URLS:
            raise SystemExit(f"T212_ENV mora biti 'live' ili 'demo', ne '{env}'")
        self.env = env
        self.base = BASE_URLS[env]

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers={"Authorization": self.auth_header})

        for attempt in range(MAX_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < MAX_RETRIES - 1:
                    wait = float(e.headers.get("Retry-After")
                                 or e.headers.get("x-ratelimit-reset")
                                 or 5 * (attempt + 1))
                    time.sleep(min(wait, 60))
                    continue
                raise T212Error(e.code, self._explain(e)) from None
        raise T212Error(429, "rate limit i nakon retryja")

    def _explain(self, e: urllib.error.HTTPError) -> str:
        if e.code == 401:
            return (f"neispravni kredencijali (shema: {self.auth_scheme}, env: {self.env}). "
                    f"Ako imaš key I secret, oba moraju biti postavljena; "
                    f"ključ vrijedi samo za okolinu u kojoj je generiran.")
        if e.code == 403:
            return "ključ nema dozvolu za ovaj endpoint (scope) ili IP nije na whitelisti"
        return e.read().decode(errors="replace")[:300]

    # --- endpointi (svi read-only) ---

    def account_summary(self) -> dict:
        """Valuta računa, cash, agregati investicija. Rate limit: 1 poziv / 5 s."""
        return self._get("/api/v0/equity/account/summary")

    def positions(self) -> list:
        """Otvorene pozicije. Svaka nosi ugniježđeni `instrument` s valutom. 1 poziv / 1 s."""
        return self._get("/api/v0/equity/positions")

    def instruments(self) -> list:
        """Katalog svih instrumenata. Spor (1 poziv / 50 s) — za M1 nije potreban,
        jer pozicije već nose valutu instrumenta."""
        return self._get("/api/v0/equity/metadata/instruments")

    # Paginacija: `nextPagePath` dok ne bude null; limit default 20, max 50.

    def history_orders(self, cursor: str | None = None, limit: int = 50) -> dict:
        return self._get("/api/v0/equity/history/orders", {"cursor": cursor, "limit": limit})

    def history_dividends(self, cursor: str | None = None, limit: int = 50) -> dict:
        return self._get("/api/v0/equity/history/dividends", {"cursor": cursor, "limit": limit})

    def history_transactions(self, cursor: str | None = None, limit: int = 50) -> dict:
        return self._get("/api/v0/equity/history/transactions", {"cursor": cursor, "limit": limit})
