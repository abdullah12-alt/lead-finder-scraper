"""Settings. Override any value with an environment variable of the same name, or put it in .env."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv(path: Path):
    """Read KEY=VALUE lines from .env; real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "leads.db"))

# SEC requires a descriptive User-Agent with contact info and <= 10 requests/sec.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "LeadGen Research admin@example.com")
SEC_MAX_RPS = float(os.environ.get("SEC_MAX_RPS", "8"))

# Browser-like UA for search engines / public LinkedIn pages.
BROWSER_USER_AGENT = os.environ.get(
    "BROWSER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36",
)

# Search APIs used for Crunchbase / executive-profile lookups. Any key that is set is used, in
# this order; DuckDuckGo Lite (no key) is the last resort but captcha-blocks after a few queries.
#   Tavily  - 1,000 free credits/month       https://tavily.com
#   SerpAPI - 250 free searches/month        https://serpapi.com
#   Serper  - 2,500 free searches (one-time) https://serper.dev
#   Brave   - ~$5 monthly credit             https://brave.com/search/api
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
SERPAPI_API_KEY = os.environ.get("SERPAPI_API_KEY", "")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
DDG_DELAY = (float(os.environ.get("DDG_DELAY_MIN", "8")), float(os.environ.get("DDG_DELAY_MAX", "15")))
DDG_COOLDOWN_MIN = int(os.environ.get("DDG_COOLDOWN_MIN", "30"))  # pause DDG after a captcha

# LinkedIn answers HTTP 999 when hit too fast; keep requests slow and back off when it happens.
LINKEDIN_DELAY = (float(os.environ.get("LINKEDIN_DELAY_MIN", "8")), float(os.environ.get("LINKEDIN_DELAY_MAX", "15")))
LINKEDIN_COOLDOWN_MIN = int(os.environ.get("LINKEDIN_COOLDOWN_MIN", "15"))

# Optional proxy for enrichment requests, e.g. "http://user:pass@host:port".
HTTP_PROXY = os.environ.get("HTTP_PROXY_URL") or None

# Re-use cached enrichment for this many days.
ENRICH_CACHE_DAYS = int(os.environ.get("ENRICH_CACHE_DAYS", "30"))
