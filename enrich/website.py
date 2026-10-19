"""Company website discovery.

Order: (1) website listed on the LinkedIn page (done by the caller), (2) a matching domain in search
results, (3) guessing domains from the name (acme.com, acme.io, getacme.com, ...) and keeping the first
one whose homepage actually mentions the company name (filters out parked/unrelated domains).
"""
import re

import httpx
from bs4 import BeautifulSoup

from enrich.match import domain_matches_name, domain_of, norm
from enrich.search import http

# Directories / social / data vendors that are never the company's own site.
BLOCKLIST = {
    "linkedin.com", "crunchbase.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "wikipedia.org", "bloomberg.com", "zoominfo.com", "dnb.com", "pitchbook.com", "sec.gov", "opencorporates.com",
    "bizapedia.com", "bizprofile.net", "nycompanyregistry.com", "cbinsights.com", "tracxn.com", "rocketreach.co",
    "apollo.io", "signalhire.com", "glassdoor.com", "indeed.com", "yelp.com", "bbb.org", "mapquest.com",
    "manta.com", "buzzfile.com", "owler.com", "craft.co", "golden.com", "wellfound.com", "angel.co",
    "f6s.com", "builtin.com", "theorg.com", "leadiq.com", "lusha.com", "contactout.com", "govcb.com",
    "corporationwiki.com", "opengovus.com", "sec.report", "fintel.io", "formds.com", "privco.com",
    "reddit.com", "medium.com", "github.com", "tiktok.com", "pinterest.com", "amazon.com", "apple.com",
    "google.com", "prnewswire.com", "businesswire.com", "globenewswire.com", "techcrunch.com",
}
PARKED_HINTS = ("domain is for sale", "buy this domain", "this domain may be for sale", "parked free",
                "godaddy", "hugedomains", "sedo.com", "dan.com")


def _blocked(url: str) -> bool:
    d = domain_of(url)
    return not d or any(d == b or d.endswith("." + b) for b in BLOCKLIST) or d.endswith(".gov")


def from_results(results: list[dict], name: str) -> str | None:
    for r in results:
        url = r.get("url", "")
        if url.startswith("http") and not _blocked(url) and domain_matches_name(url, name):
            return f"https://{domain_of(url)}"
    return None


def candidate_domains(name: str) -> list[str]:
    words = norm(name).split()
    if not words:
        return []
    compact, hyphen = "".join(words), "-".join(words)
    cands = [f"{compact}.com", f"{hyphen}.com", f"{compact}.io", f"{compact}.ai", f"{compact}.co",
             f"get{compact}.com", f"{compact}hq.com", f"try{compact}.com"]
    if len(words) > 1 and words[-1] in {"technologies", "technology", "labs", "health", "therapeutics", "ai",
                                        "bio", "software", "systems", "group", "holdings", "solutions"}:
        short = "".join(words[:-1])
        cands += [f"{short}.com", f"{short}.ai", f"{short}.io"]
    return list(dict.fromkeys(c for c in cands if len(c) > 6))


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _homepage_matches(domain: str, name: str, city: str | None, phone: str | None) -> bool:
    """Accept a guessed domain only with real evidence it belongs to this company."""
    target = norm(name).replace(" ", "")
    if not target:
        return False
    try:
        resp = http().get(f"https://{domain}", timeout=8)
    except (httpx.HTTPError, ValueError):
        return False
    final = domain_of(str(resp.url))
    if not (final == domain or final.endswith("." + domain)):
        return False  # redirected elsewhere (often a marketplace / parked page)
    html = resp.text[:300_000]
    if resp.status_code == 403 and "just a moment" in html.lower():
        # Cloudflare challenge: the site is live but unreadable. Trust only exact, distinctive names.
        return domain == f"{target}.com" and len(target) >= 8
    if resp.status_code >= 400 or any(h in html.lower() for h in PARKED_HINTS):
        return False

    soup = BeautifulSoup(html, "html.parser")
    branding = " ".join(filter(None, [
        soup.title.get_text() if soup.title else "",
        *(m.get("content", "") for m in soup.find_all("meta", attrs={"property": ["og:site_name", "og:title"]})),
    ]))
    if target in _compact(branding):
        return True
    # Name only in body text: also require the Form D city or phone number on the page.
    text = soup.get_text(" ").lower()
    if target not in _compact(text):
        return False
    digits = re.sub(r"\D", "", phone or "")[-7:]
    return bool((city and city.lower() in text) or (len(digits) == 7 and digits in re.sub(r"\D", "", text)))


def guess(name: str, city: str | None = None, phone: str | None = None) -> str | None:
    for domain in candidate_domains(name):
        if _homepage_matches(domain, name, city, phone):
            return f"https://{domain}"
    return None
