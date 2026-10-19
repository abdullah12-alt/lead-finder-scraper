"""Enrich one company (by its latest filing) with website, LinkedIn, Crunchbase and exec profile links.

- LinkedIn company: slug guessing verified against the public page (no search engine needed).
- Website: from the LinkedIn page, else guessed domains verified by homepage content, else search.
- Crunchbase / executive profiles: search API (or DuckDuckGo fallback).
A step that was blocked (LinkedIn 999, search captcha) isn't marked as attempted, so it's retried later.
"""
from datetime import datetime, timezone

import config
import db
from enrich import crunchbase, linkedin, website
from enrich.linkedin import LinkedInBlocked
from enrich.match import clean_name, domain_of
from enrich.search import SearchUnavailable, search

OPTIONS = ("website", "linkedin", "crunchbase", "people")
MAX_PEOPLE = 3
COLUMNS = ["cik", "website", "linkedin_url", "linkedin_industry", "linkedin_size", "linkedin_employees",
           "linkedin_followers", "linkedin_founded", "linkedin_hq", "linkedin_description",
           "crunchbase_url", "crunchbase_snippet", "notes", "updated_at"]


def _attempted(notes: str | None) -> set[str]:
    return set((notes or "").removeprefix("attempted:").split(",")) - {""}


def enrich_company(filing, options: set[str]) -> list[str]:
    """Returns a list of problems (e.g. rate limits) for the job log."""
    conn = db.get_conn()
    cik, name = filing["cik"], filing["entity_name"] or ""
    options = options & set(OPTIONS)
    row = conn.execute("SELECT * FROM enrichment WHERE cik=? AND updated_at >= datetime('now', ?)",
                       (cik, f"-{config.ENRICH_CACHE_DAYS} days")).fetchone()
    if not options or not name or (row and options <= _attempted(row["notes"])):
        return []

    existing = conn.execute("SELECT * FROM enrichment WHERE cik=?", (cik,)).fetchone()
    data = dict(existing) if existing else {"cik": cik}
    done, problems = set(), []

    if "linkedin" in options:
        try:
            info = linkedin.find_company(name, filing["city"], filing["state"])
            if info:
                data.update({
                    "linkedin_url": info["url"],
                    "linkedin_industry": info.get("industry"),
                    "linkedin_size": info.get("size"),
                    "linkedin_employees": info.get("employees"),
                    "linkedin_followers": info.get("followers"),
                    "linkedin_founded": info.get("founded"),
                    "linkedin_hq": info.get("hq"),
                    "linkedin_description": (info.get("description") or "")[:1000] or None,
                })
                site = info.get("website")
                if site and "linkedin.com" not in site:
                    data["website"] = f"https://{domain_of(site if '//' in site else 'https://' + site)}"
            done.add("linkedin")
        except (LinkedInBlocked, SearchUnavailable) as exc:
            problems.append(str(exc))

    if "website" in options:
        if not data.get("website"):
            data["website"] = website.guess(name, filing["city"], filing["phone"])
        if not data.get("website"):
            try:
                location = " ".join(x for x in (filing["city"], filing["state"]) if x)
                data["website"] = website.from_results(search(f'"{clean_name(name)}" {location}'.strip()), name)
            except SearchUnavailable:
                pass  # domain guessing already ran; search is a bonus
        done.add("website")

    if "crunchbase" in options:
        try:
            url, snippet = crunchbase.find(name)
            data["crunchbase_url"] = url or data.get("crunchbase_url")
            data["crunchbase_snippet"] = snippet or data.get("crunchbase_snippet")
            done.add("crunchbase")
        except SearchUnavailable as exc:
            problems.append(f"Crunchbase: {exc}")

    if "people" in options:
        execs = conn.execute(
            "SELECT first_name, last_name FROM persons WHERE accession=? AND relationships LIKE '%Executive%' "
            "ORDER BY seq LIMIT ?", (filing["accession"], MAX_PEOPLE)).fetchall()
        try:
            for p in execs:
                full = " ".join(x for x in (p["first_name"], p["last_name"]) if x)
                if not full or conn.execute("SELECT 1 FROM person_links WHERE cik=? AND full_name=?",
                                            (cik, full)).fetchone():
                    continue
                url = linkedin.find_person_url(full, name)
                with conn:
                    conn.execute("INSERT OR REPLACE INTO person_links(cik, full_name, linkedin_url, updated_at) "
                                 "VALUES (?,?,?,datetime('now'))", (cik, full, url))
            done.add("people")
        except SearchUnavailable as exc:
            problems.append(f"People: {exc}")

    data["notes"] = "attempted:" + ",".join(sorted(_attempted(data.get("notes")) | done))
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with conn:
        conn.execute(f"INSERT OR REPLACE INTO enrichment ({','.join(COLUMNS)}) "
                     f"VALUES ({','.join('?' * len(COLUMNS))})", [data.get(c) for c in COLUMNS])
    return problems
