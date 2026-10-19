"""Shared SEC HTTP client (rate-limited, polite User-Agent) and value parsers."""
import threading
import time
from datetime import datetime

import httpx

import config

_lock = threading.Lock()
_last_request = 0.0
_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers={"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=60,
            follow_redirects=True,
        )
    return _client


def sec_get(url: str, retries: int = 3) -> httpx.Response:
    """GET from sec.gov, throttled to SEC_MAX_RPS across all threads."""
    global _last_request
    for attempt in range(retries):
        with _lock:
            wait = (1.0 / config.SEC_MAX_RPS) - (time.monotonic() - _last_request)
            if wait > 0:
                time.sleep(wait)
            _last_request = time.monotonic()
        try:
            resp = _get_client().get(url)
        except httpx.TransportError:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code in (429, 503):
            # SEC throttles with 429/503; back off and retry.
            time.sleep(10 * (attempt + 1))
            continue
        return resp
    return resp


def parse_amount(value) -> tuple[float | None, bool]:
    """Return (amount, is_indefinite)."""
    if value is None:
        return None, False
    s = str(value).strip().replace(",", "").replace("$", "")
    if not s:
        return None, False
    if s.lower().startswith("indefinite"):
        return None, True
    try:
        return float(s), False
    except ValueError:
        return None, False


def parse_int(value) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def parse_bool(value) -> int:
    return 1 if str(value).strip().lower() in ("true", "yes", "1", "y") else 0


def parse_date(value) -> str | None:
    """Accept '2026-10-02', '30-SEP-2026', '20261016'; return ISO date."""
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None
