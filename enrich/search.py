"""Web search for enrichment: free-tier search APIs first, DuckDuckGo Lite as a no-key last resort.

Plain HTML scraping of search engines is unreliable (DuckDuckGo captcha-blocks after a few queries,
Bing serves junk results to scripts), so an API key is strongly recommended for Crunchbase/people.
"""
import json
import random
import threading
import time

import httpx
from bs4 import BeautifulSoup

import config
import db


class Blocked(Exception):
    """The engine refused the request (captcha, quota, bad key)."""


class SearchUnavailable(Exception):
    """Every search engine is blocked or cooling down; retry the lookup later."""


_lock = threading.Lock()
_last_call: dict[str, float] = {}
_cooldown_until: dict[str, float] = {}
_client: httpx.Client | None = None


def http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers={"User-Agent": config.BROWSER_USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            timeout=25,
            follow_redirects=True,
            proxy=config.HTTP_PROXY,
        )
    return _client


def throttle(key: str, delay: tuple[float, float]):
    with _lock:
        wait = random.uniform(*delay) - (time.monotonic() - _last_call.get(key, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call[key] = time.monotonic()


def cooling_down(key: str) -> bool:
    return time.monotonic() < _cooldown_until.get(key, 0)


def cool_down(key: str, minutes: int):
    _cooldown_until[key] = time.monotonic() + minutes * 60


def _result(url, title, snippet):
    return {"url": url or "", "title": title or "", "snippet": snippet or ""}


def _tavily(query: str) -> list[dict]:
    resp = http().post("https://api.tavily.com/search",
                       headers={"Authorization": f"Bearer {config.TAVILY_API_KEY}"},
                       json={"query": query, "max_results": 10, "search_depth": "basic"})
    if resp.status_code != 200:
        raise Blocked(f"Tavily returned {resp.status_code}")
    return [_result(r.get("url"), r.get("title"), r.get("content")) for r in resp.json().get("results", [])]


def _serpapi(query: str) -> list[dict]:
    resp = http().get("https://serpapi.com/search.json",
                      params={"engine": "google", "q": query, "api_key": config.SERPAPI_API_KEY, "num": 10})
    if resp.status_code != 200:
        raise Blocked(f"SerpAPI returned {resp.status_code}")
    return [_result(r.get("link"), r.get("title"), r.get("snippet")) for r in resp.json().get("organic_results", [])]


def _serper(query: str) -> list[dict]:
    resp = http().post("https://google.serper.dev/search", headers={"X-API-KEY": config.SERPER_API_KEY},
                       json={"q": query, "num": 10})
    if resp.status_code != 200:
        raise Blocked(f"Serper returned {resp.status_code}")
    return [_result(r.get("link"), r.get("title"), r.get("snippet")) for r in resp.json().get("organic", [])]


def _brave(query: str) -> list[dict]:
    resp = http().get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": 10},
                      headers={"X-Subscription-Token": config.BRAVE_API_KEY, "Accept": "application/json"})
    if resp.status_code != 200:
        raise Blocked(f"Brave returned {resp.status_code}")
    return [_result(r.get("url"), r.get("title"), r.get("description"))
            for r in resp.json().get("web", {}).get("results", [])]


def _ddg(query: str) -> list[dict]:
    resp = http().post("https://lite.duckduckgo.com/lite/", data={"q": query, "kl": "us-en"})
    if resp.status_code != 200 or "anomaly-modal" in resp.text:
        cool_down("ddg", config.DDG_COOLDOWN_MIN)
        raise Blocked(f"DuckDuckGo captcha (HTTP {resp.status_code}); pausing {config.DDG_COOLDOWN_MIN} min")
    soup = BeautifulSoup(resp.text, "html.parser")
    results = []
    for a in soup.select("a.result-link"):
        snippet_td = a.find_next("td", class_="result-snippet")
        results.append(_result(a.get("href"), a.get_text(" ", strip=True),
                               snippet_td.get_text(" ", strip=True) if snippet_td else ""))
    return results


# (name, function, key, per-call delay)
ENGINES = [
    ("tavily", _tavily, lambda: config.TAVILY_API_KEY, (0.2, 0.5)),
    ("serpapi", _serpapi, lambda: config.SERPAPI_API_KEY, (0.2, 0.5)),
    ("serper", _serper, lambda: config.SERPER_API_KEY, (0.2, 0.5)),
    ("brave", _brave, lambda: config.BRAVE_API_KEY, (1.1, 1.5)),
    ("ddg", _ddg, lambda: True, config.DDG_DELAY),
]


def available_engines() -> list[str]:
    return [name for name, _, key, _ in ENGINES if key()]


def search(query: str, cache_days: int = 14) -> list[dict]:
    """Return [{url, title, snippet}] ([] = genuinely no results). Raises SearchUnavailable if all engines fail."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT results FROM search_cache WHERE query=? AND created_at >= datetime('now', ?) "
        "ORDER BY created_at DESC LIMIT 1", (query, f"-{cache_days} days")).fetchone()
    if row:
        return json.loads(row["results"])

    for name, fn, key, delay in ENGINES:
        if not key() or cooling_down(name):
            continue
        throttle(name, delay)
        try:
            results = fn(query)
        except (Blocked, httpx.HTTPError, ValueError) as exc:
            print(f"search[{name}] failed: {exc}")
            continue
        # Cache only genuine answers (including "no results") so blocks are retried later.
        with conn:
            conn.execute("INSERT OR REPLACE INTO search_cache(engine, query, results, created_at) "
                         "VALUES (?,?,?,datetime('now'))", (name, query, json.dumps(results)))
        return results
    raise SearchUnavailable("all search engines are blocked or rate-limited")
