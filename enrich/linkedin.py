"""LinkedIn lookups WITHOUT logging in.

Company: guess the vanity slug from the company name ("CredibleMind Inc." -> /company/crediblemind),
then verify it against the public, logged-out page (JSON-LD name + headquarters). If guessing fails,
fall back to search results. LinkedIn answers HTTP 999 when hit too fast, so requests are slow and
a 999 pauses LinkedIn lookups for LINKEDIN_COOLDOWN_MIN minutes.

People: profile URLs come only from search results; profiles are never opened.
"""
import json
import re

import httpx
from bs4 import BeautifulSoup

import config
from enrich.match import clean_name, norm, similarity
from enrich.search import cool_down, cooling_down, http, search, throttle

COMPANY_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/([^/?#]+)", re.I)
PERSON_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/?#]+)", re.I)

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


class LinkedInBlocked(Exception):
    pass


def candidate_slugs(name: str) -> list[str]:
    words = norm(name).split()
    if not words:
        return []
    slugs = ["".join(words), "-".join(words)]
    return list(dict.fromkeys(s for s in slugs if len(s) >= 3))


def fetch_company(url: str) -> dict | None:
    """Read the public company page. Returns None if the page doesn't exist; raises LinkedInBlocked on 999."""
    if cooling_down("linkedin"):
        raise LinkedInBlocked("LinkedIn lookups paused after rate limit")
    throttle("linkedin", config.LINKEDIN_DELAY)
    try:
        resp = http().get(url)
    except httpx.HTTPError:
        return None
    if resp.status_code == 999 or resp.status_code == 429 or "authwall" in str(resp.url):
        cool_down("linkedin", config.LINKEDIN_COOLDOWN_MIN)
        raise LinkedInBlocked(f"LinkedIn returned {resp.status_code}; pausing {config.LINKEDIN_COOLDOWN_MIN} min")
    if resp.status_code != 200:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    info: dict = {"url": url}
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except ValueError:
            continue
        for node in data.get("@graph", [data]) if isinstance(data, dict) else []:
            if node.get("@type") != "Organization":
                continue
            info["name"] = node.get("name")
            same_as = node.get("sameAs")
            info["website"] = same_as if isinstance(same_as, str) else None
            info["employees"] = (node.get("numberOfEmployees") or {}).get("value")
            info["description"] = node.get("description")
            addr = node.get("address") or {}
            info["city"], info["region"] = addr.get("addressLocality"), addr.get("addressRegion")
            info["hq"] = ", ".join(x for x in (info["city"], info["region"]) if x)

    labels = {"Website": "website", "Industry": "industry", "Company size": "size",
              "Headquarters": "hq", "Founded": "founded"}
    for dt in soup.select("dl dt"):
        key = labels.get(dt.get_text(strip=True))
        dd = dt.find_next("dd")
        if not key or not dd:
            continue
        value = dd.get_text(" ", strip=True)
        if key == "website":
            info["website"] = info.get("website") or value.split(" ")[0]
        else:
            info[key] = info.get(key) or value
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and (m := re.search(r"([\d,]+) followers", meta.get("content", ""))):
        info["followers"] = m.group(1)
    return info if info.get("name") else None


def _location_score(info: dict, city: str | None, state: str | None) -> int:
    """1 = matches Form D address, 0 = page has no location, -1 = different location."""
    hq = (info.get("hq") or "").lower()
    if not hq:
        return 0
    state_full = US_STATES.get((state or "").upper(), "").lower()
    if (city and city.lower() in hq) or (state and (state.lower() == (info.get("region") or "").lower()
                                                    or (state_full and state_full in hq))):
        return 1
    return -1


def _verified(info: dict | None, name: str, city, state) -> bool:
    if not info:
        return False
    sim = similarity(info.get("name", ""), name)
    loc = _location_score(info, city, state)
    # A page in a different city/state is treated as a different company with a similar name.
    return (sim >= 0.8 and loc >= 0) or (sim >= 0.7 and loc == 1)


def find_company(name: str, city: str | None, state: str | None) -> dict | None:
    """Return verified company info (incl. 'url') or None. Raises LinkedInBlocked when rate-limited."""
    for slug in candidate_slugs(name):
        info = fetch_company(f"https://www.linkedin.com/company/{slug}")
        if _verified(info, name, city, state):
            return info

    # Fall back to search results for the URL, then verify the page the same way.
    for r in search(f'site:linkedin.com/company "{clean_name(name)}"'):
        m = COMPANY_RE.match(r.get("url", ""))
        if not m or m.group(1) in candidate_slugs(name):
            continue
        title_name = re.split(r"\s+[|\-–]\s+", r.get("title", ""))[0]
        if similarity(title_name, name) < 0.7 and similarity(m.group(1).replace("-", " "), name) < 0.7:
            continue
        info = fetch_company(f"https://www.linkedin.com/company/{m.group(1)}")
        if _verified(info, name, city, state):
            return info
        break
    return None


def find_person_url(full_name: str, company: str) -> str | None:
    last = full_name.split()[-1].lower() if full_name.split() else ""
    for r in search(f'site:linkedin.com/in "{full_name}" "{clean_name(company)}"'):
        m = PERSON_RE.match(r.get("url", ""))
        if m and last and last in (r.get("title", "") + " " + m.group(1)).lower():
            return f"https://www.linkedin.com/in/{m.group(1)}"
    return None
