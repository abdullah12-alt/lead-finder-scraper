"""Company-name normalisation and fuzzy matching."""
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

_SUFFIXES = {
    "inc", "incorporated", "llc", "l l c", "corp", "corporation", "co", "company", "ltd", "limited",
    "lp", "l p", "llp", "plc", "pbc", "gmbh", "sa", "ag", "bv", "the",
}


def clean_name(name: str) -> str:
    """'Acme Robotics, Inc.' -> 'Acme Robotics' (keeps original casing for search queries)."""
    s = re.sub(r"\s+", " ", (name or "").replace("&amp;", "&")).strip(" ,.")
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"\s*/[A-Za-z]{2,3}/?$", "", s)  # EDGAR state-of-inc suffix, e.g. "Acme Inc./DE"
    words = re.split(r"[\s,]+", s)
    while words and re.sub(r"[^a-z]", "", words[-1].lower()) in {w.replace(" ", "") for w in _SUFFIXES}:
        words.pop()
    return " ".join(words).strip(" ,.-") or s


def norm(name: str) -> str:
    s = clean_name(name).lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similarity(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    compact_a, compact_b = a.replace(" ", ""), b.replace(" ", "")
    if compact_a == compact_b:
        return 1.0
    return ratio


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def domain_matches_name(url: str, name: str) -> bool:
    """True if the domain's main label plausibly belongs to the company."""
    label = domain_of(url).split(".")[0].replace("-", "")
    words = [w for w in norm(name).split() if len(w) >= 3]
    compact = norm(name).replace(" ", "")
    if not label or not compact:
        return False
    if label in compact or compact in label:
        return True
    if words and words[0] in label:
        return True
    return SequenceMatcher(None, label, compact).ratio() >= 0.7
