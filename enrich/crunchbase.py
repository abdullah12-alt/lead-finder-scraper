"""Crunchbase lookup without an account or API.

crunchbase.com itself is behind Cloudflare bot protection and its ToS forbid scraping, so we
never fetch it. The profile URL + snippet come from search-engine results only. Funding data
itself comes from Form D.
"""
import re

from enrich.match import clean_name, similarity
from enrich.search import search

ORG_RE = re.compile(r"https?://(?:www\.)?crunchbase\.com/organization/([^/?#]+)", re.I)


def _summary(snippet: str, title_name: str) -> str | None:
    """Search APIs like Tavily return page text; keep only the company's own description."""
    text = re.sub(r"\s+", " ", snippet or "").strip()
    m = re.search(r"## About [^.]+\.\s*(.+?)(?:\s*##|$)", text)
    if m:
        text = m.group(1)
    text = re.sub(r"^Title: .*? - Crunchbase[^.]*?(?=[A-Z])", "", text).strip()
    if "Crunchbase Pro" in text[:200] and title_name and title_name in text:
        text = text[text.index(title_name):]
    return (text[:300].rsplit(" ", 1)[0] + "...") if len(text) > 300 else (text or None)


def from_results(results: list[dict], name: str) -> tuple[str | None, str | None]:
    best, best_score = (None, None), 0.0
    for r in results:
        m = ORG_RE.match(r.get("url", ""))
        if not m:
            continue
        title_name = re.split(r"\s+-\s+Crunchbase", r.get("title", ""), flags=re.I)[0].removeprefix("Title: ")
        score = max(similarity(title_name, name), similarity(m.group(1).replace("-", " "), name))
        if score > best_score:
            best_score = score
            best = (f"https://www.crunchbase.com/organization/{m.group(1)}", _summary(r.get("snippet"), title_name))
    # Strict: "Inside Reality" vs "Unseen Reality" scores ~0.8 and must not match.
    return best if best_score >= 0.9 else (None, None)


def find(name: str) -> tuple[str | None, str | None]:
    """Raises SearchUnavailable (from search) when no engine can answer."""
    return from_results(search(f'site:crunchbase.com/organization "{clean_name(name)}"'), name)
