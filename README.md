# Lead Finder

Lead Finder finds companies that have just raised money, so sales can reach them while they have budget to spend.

It reads **SEC Form D filings**, which are free, public, and need no account. Every US company that raises private money under Regulation D must file one. It lets you filter those companies by industry, location, raise size, round timing and more. It can then **enrich** each company with its website, LinkedIn page, Crunchbase profile and executives' LinkedIn profiles, all without paid LinkedIn or Crunchbase accounts.

---

## Contents

1. [Quick start](#1-quick-start)
2. [How it works (overview)](#2-how-it-works-overview)
3. [Using the app](#3-using-the-app)
4. [Step 1: Loading SEC data](#4-step-1-loading-sec-data)
5. [Step 2: Filtering](#5-step-2-filtering)
6. [Step 3: Enrichment](#6-step-3-enrichment)
7. [Step 4: Results and export](#7-step-4-results-and-export)
8. [Background jobs](#8-background-jobs)
9. [Caching and re-runs](#9-caching-and-re-runs)
10. [Configuration (.env)](#10-configuration-env)
11. [Command line and scheduling](#11-command-line-and-scheduling)
12. [Project structure](#12-project-structure)
13. [Database](#13-database)
14. [Limits, costs and legal notes](#14-limits-costs-and-legal-notes)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Quick start

Requirements: Python 3.11 or newer (tested on 3.13), Windows/macOS/Linux, internet access.

These steps set the project up on any laptop. The virtual environment (`.venv`) is a private folder of Python packages for this project only, so it never clashes with other projects. It is not copied between machines, so **create it fresh on each laptop**. When you copy the project, leave out the `.venv/` and `data/` folders.

### Step 1: Check Python is installed

```powershell
python --version
```

It should print `Python 3.11` or newer. If not, install Python from https://www.python.org/downloads/. On Windows, tick **"Add python.exe to PATH"** in the installer. On macOS/Linux, use `python3` instead of `python` if `python` isn't found.

### Step 2: Open a terminal in the project folder

```powershell
cd "D:\Learning Projects\scraping"     # use your own path
```

### Step 3: Create the virtual environment (once per laptop)

```powershell
python -m venv .venv
```

This creates a `.venv` folder inside the project.

### Step 4: Activate the virtual environment (every time you open a new terminal)

| System | Command |
|---|---|
| Windows (PowerShell) | `.venv\Scripts\Activate.ps1` |
| Windows (Command Prompt) | `.venv\Scripts\activate.bat` |
| macOS / Linux | `source .venv/bin/activate` |

When it works, the prompt starts with `(.venv)`. To leave it later, type `deactivate`.

> **PowerShell says "running scripts is disabled on this system"?** Run this once, then activate again:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```

### Step 5: Install the packages (once per laptop)

With `(.venv)` showing in the prompt:

```powershell
pip install -r requirements.txt
```

### Step 6: Create the `.env` settings file

Create a file named `.env` in the project folder:

```ini
SEC_USER_AGENT=YourCompany LeadGen you@yourcompany.com
TAVILY_API_KEY=tvly-...
```

- `SEC_USER_AGENT` is **required**. The SEC asks every automated client to identify itself with a name and contact email, and may block requests without one.
- `TAVILY_API_KEY` is optional but recommended. It enables Crunchbase and executive lookups (free tier: 1,000 searches a month at https://tavily.com).

### Step 7: Start the app

With the virtual environment active:

```powershell
python app.py
```

Without activating, call the environment's Python directly: `.venv\Scripts\python app.py` (Windows) or `.venv/bin/python app.py` (macOS/Linux).

Leave the terminal open while you use the app. Press **Ctrl+C** to stop it.

### Step 8: Use it

Open **http://127.0.0.1:5000**, then:

1. Go to **Data & jobs**. Load one or two **quarterly data sets**, then **Fetch filings** for the last 7–14 days.
2. Go to **New search**. Pick at least one industry, set any optional filters, choose enrichment, and click **Find leads**.
3. When the job finishes, browse the results and click **CSV** or **Excel** to export.

---

## 2. How it works (overview)

### What is Form D?

When a US company raises private money from investors (for example a seed or Series A round), it must file a short form with the **SEC**, the US government's financial regulator. That form is called **Form D**.

Form D filings are public and free to read, with no account needed. Each one tells us:

- the company's **name, address and phone number**
- its **industry** and type (corporation, LLC, and so on)
- **how much it is raising** and **how much it has raised so far**
- **when the first sale happened**, which shows how fresh the round is
- the **names of its executives and directors**

In short, Form D tells us **who just got funded, how much, and when**. A company that just raised money has budget to spend, which makes it a good sales lead.

### Where and how the data is collected

1. **Load from the SEC.** The scraper downloads Form D filings from SEC EDGAR, the SEC's public filing website, in two ways:
   - **Quarterly data sets:** one file per quarter with every Form D filing (about 15,000 companies), loaded in seconds.
   - **Daily filings:** for the newest companies, it reads the SEC's daily list of new filings and downloads each new Form D.

   It follows the SEC's rules: it identifies itself on every request and stays under the SEC's speed limit. Everything is saved in a local database, so searches are instant.
2. **Filter.** You choose industry, location, raise size, timing and more, and the scraper returns the matching companies.
3. **Enrich.** Form D has no website or LinkedIn link, so the scraper adds them **without logging in to any site and without paid accounts**:
   - **LinkedIn company page:** guesses the company's LinkedIn address from its name, opens the public page, and checks that the name and city match the filing.
   - **Website:** taken from LinkedIn, or found by trying likely domains (like `companyname.com`) and confirming the site belongs to the company.
   - **Crunchbase and executives:** found through a normal web search on a free plan. Crunchbase itself is never opened.

   Every match is checked before it is saved, because a wrong link is worse than no link.
4. **Export.** The results table can be downloaded as Excel or CSV for the sales team.

### Technical flow

```
                 ┌──────────────────────────── SEC EDGAR (free, public) ───────────────────────────┐
                 │  Quarterly Form D data sets (ZIP of TSV tables)   Daily index + primary_doc.xml │
                 └───────────────────────────────────┬─────────────────────────────────────────────┘
                                                     │ 1. Load
                                                     ▼
                                       ┌───────────────────────────┐
                                       │  SQLite (data/leads.db)   │
                                       │  filings, persons, cache  │
                                       └─────────────┬─────────────┘
                                                     │ 2. Filter (SQL, latest filing per company)
                                                     ▼
                                       ┌───────────────────────────┐
                                       │   Shortlist of companies  │
                                       └─────────────┬─────────────┘
                                                     │ 3. Enrich (top N only, optional)
          ┌─────────────────────┬────────────────────┼──────────────────────┬──────────────────────┐
          ▼                     ▼                    ▼                      ▼                      ▼
   LinkedIn company page   Website            Crunchbase profile     Executive LinkedIn     (cached 30 days)
   slug guess + verify     LinkedIn, domain   search API results     search API results
   (public page, no login) guess, or search   (site never fetched)   (profiles never opened)
                                                     │
                                                     ▼ 4. Results table + CSV / Excel export
```

**Why the SEC is the main source.** Every targeting filter you need (industry, fund vs. operating company, location, raise size, sold ratio, incorporation year, entity type, revenue range, security type, investor count, date of first sale) is a field on Form D. The filing also contains the company's **phone number, street address and named executives**. This is the "who raised, how much, when" data that Crunchbase charges for, taken from the primary source.

**Why LinkedIn and Crunchbase are only "enrichment".** Both forbid scraping and actively block bots. Lead Finder never logs in to either site. It only adds links and short public details to companies you have already filtered, and it treats that data as best-effort.

---

## 3. Using the app

The app has four screens, reached from the left sidebar.

### New search (`/`)

| Area | What it does |
|---|---|
| **Industries** (required) | Clickable chips for the fixed SEC industry list, grouped as on the form. The **Tech** and **Life sciences** presets select common groups; **Clear** resets them. |
| **Exclude pooled investment funds** | On by default. Removes VC, PE, hedge and real-estate fund filings, which are about two-thirds of all Form D filings. |
| **Filters** (all optional) | Collapsible sections: Location, Raise size, Round timing, Company profile, Security types, Revenue range. Empty fields are ignored. Quick buttons fill common values (for example "$1M to $5M" or "90 days"). |
| **Enrichment** | Tiles to switch on Website, LinkedIn page, Crunchbase and Executive profiles, plus "Enrich the top N companies" (default 50). It also shows which search engines are active. |
| **Search summary** (right panel) | Lists every active filter as you change it. **Find leads** stays disabled until at least one industry is selected. |

### Job progress (`/jobs/<id>`)

This screen shows a progress bar and three steps: **Filter filings**, **Enrich companies**, **Ready**. It updates every 1.5 seconds and opens the results automatically when a search finishes.

### Results (`/results/<id>`)

The page shows summary cards (companies, total sold, median offering, websites found, LinkedIn found) and a table you can sort and filter. Each row has icon links to the website, LinkedIn, Crunchbase and the original SEC filing. If enrichment is still running, a banner shows progress and the page reloads itself when it finishes. Results appear as soon as filtering is done; enrichment fills in afterwards.

### Data & jobs (`/admin`)

This screen shows database coverage (number of filings and companies, earliest and latest filing date). It also has controls to fetch recent daily filings or load quarterly data sets, and a table of recent jobs with their status.

The sidebar also lists your **recent searches** with result counts, so you can reopen past results.

---

## 4. Step 1: Loading SEC data

Code: [edgar/](edgar/)

### Quarterly data sets (bulk history)

File: [edgar/bulk_loader.py](edgar/bulk_loader.py)

1. Reads the SEC listing page (`sec.gov/data-research/sec-markets-data/form-d-data-sets`) to find the ZIP for each quarter (for example `2026q3_d.zip`).
2. Downloads the ZIP (about 4 MB) and keeps a copy in `data/raw/`.
3. Reads its tab-separated tables:
   - `FORMDSUBMISSION`: filing date and form type (`D` or `D/A` amendment)
   - `ISSUERS`: company name, CIK, address, phone, entity type, incorporation year. Only the *primary* issuer is kept.
   - `OFFERING`: industry, fund type, revenue range, security types, amounts, investor count, date of first sale
   - `RELATEDPERSONS`: executives, directors and promoters with their roles
4. Skips test filings, cleans the values (see below) and saves everything in one database transaction.

A quarter is about 15,000–16,000 filings and loads in a few seconds after downloading.

### Recent filings (daily index)

File: [edgar/daily_fetcher.py](edgar/daily_fetcher.py)

Quarterly data sets are published after each quarter ends, so the newest filings come from the **EDGAR daily index**:

1. For each day in the range, downloads `Archives/edgar/daily-index/<year>/QTR<n>/form.<yyyymmdd>.idx`. Weekends and holidays have no index (404) and are skipped.
2. Keeps lines whose form type is `D` or `D/A`, and drops filings already in the database.
3. Downloads each filing's `primary_doc.xml` and parses the same fields as the bulk loader.

Speed is about 300 filings per business day at 8 requests per second, so roughly 2 minutes per business day.

### SEC rules respected

- Every request sends `SEC_USER_AGENT`.
- All SEC requests go through one throttle at **8 requests/second** across all threads (the SEC allows 10).
- HTTP 429/503 responses are retried with increasing back-off.

### Value cleaning

| Raw value | Stored as |
|---|---|
| `"Indefinite"` offering amount | `total_offering = NULL`, `offering_indefinite = 1` |
| Dates (`30-SEP-2026`, `2026-10-02`, `20261016`) | ISO `YYYY-MM-DD` |
| Incorporation year | Exact year if "within five years"; otherwise `inc_over_five = 1` with no year (Form D doesn't ask for it) |
| `true` / `false` flags | `1` / `0` |
| CIK with leading zeros | Leading zeros removed |

---

## 5. Step 2: Filtering

Code: [filters.py](filters.py)

The search form is turned into one SQL query.

**One row per company.** A company can file several times (new rounds, `D/A` amendments). The query ranks each company's filings by filing date and keeps only the **latest** before applying filters. This is why an amendment replaces the original.

**Sort order.** Newest date of first sale first (filing date if no sale yet), then largest amount sold.

| Parameter | Form D field | Rule |
|---|---|---|
| `industries` (**required**) | Industry group | Matches any selected industry |
| `exclude_funds` | Industry = "Pooled Investment Fund", or security type "pooled investment fund interests" | Both are excluded when on |
| `states` | Issuer state/country code | Comma-separated, e.g. `CA, NY, TX` (case-insensitive) |
| `cities` | Issuer city | Comma-separated; exact name, case-insensitive |
| `raise_basis` | — | Applies min/max to **total offering** (target) or **amount sold** (actually raised) |
| `min_raise` / `max_raise` | Total offering or total sold | Dollar range. Indefinite offerings never match an offering-based range |
| `min_sold_ratio` | Total sold ÷ total offering | Enter `50` or `0.5` for 50%. Indefinite offerings are excluded |
| `inc_year_min` / `inc_year_max` | Year of incorporation | Companies incorporated more than 5 years ago have no exact year, so they pass "to" but never "from" |
| `entity_types` | Entity type | Corporation, LLC, LP, General Partnership, Business Trust, Other |
| `revenue_ranges` | Revenue range | Most filers choose "Decline to Disclose", so this filter removes many results |
| `security_types` | Equity, debt, option, etc. | Matches filings offering **any** selected type |
| `min_investors` | Number of investors so far | At least this many |
| `first_sale_within_days` | Date of first sale | Within the last N days. Filings with no sale yet are excluded |
| `filed_within_days` | Filing date | Within the last N days |
| `limit` | — | Maximum results (default 500) |

**Tips for good leads:**
- *Early-stage startups:* Other Technology + Computers, exclude funds, Corporation, Equity, $1M–$20M, first sale within 90 days.
- *Rounds that actually closed:* minimum sold ratio 80%+.
- *Young companies:* incorporated from (current year − 3).

---

## 6. Step 3: Enrichment

Code: [enrich/](enrich/), coordinated by [enrich/\_\_init\_\_.py](enrich/__init__.py)

Enrichment runs only on the **top N** results (default 50) and is optional. Every lookup is verified before it is saved, because a wrong website or LinkedIn page is worse than none.

### LinkedIn company page (no key needed)

File: [enrich/linkedin.py](enrich/linkedin.py)

1. **Guess the company handle** from the cleaned name. Suffixes like Inc., LLC and `/DE` are removed: "CredibleMind Inc." gives `crediblemind` and `credible-mind`.
2. **Open the public, logged-out page** `linkedin.com/company/<handle>` and read its structured data (name, headquarters, website, employee count, description) and its About section (industry, company size, founded year, followers).
3. **Verify** the page is the same company:
   - name similarity ≥ 0.8 and the headquarters doesn't conflict with the Form D city/state, **or**
   - name similarity ≥ 0.7 and the headquarters matches the Form D city or state.
   A page in a different city or state counts as a different company with a similar name.
4. If guessing fails, search for `site:linkedin.com/company "<name>"` and verify the result the same way.

**Rate limiting.** LinkedIn answers **HTTP 999** after a few quick requests from one IP. Lead Finder waits 8–15 seconds between LinkedIn requests. On a 999 it **pauses LinkedIn lookups for 15 minutes** and carries on with the other steps. The skipped companies aren't marked as done, so they're retried on the next run.

### Website (no key needed)

File: [enrich/website.py](enrich/website.py)

Tried in this order, stopping at the first success:

1. **The website listed on the verified LinkedIn page.**
2. **Domain guessing.** The name is turned into candidate domains: `name.com`, `name-name.com`, `name.io`, `name.ai`, `name.co`, `getname.com`, `namehq.com`, `tryname.com`. For names ending in Technologies, Labs, Health, AI and similar, the short form is tried too (`acme.com` for "Acme Technologies"). A candidate is accepted only if its homepage:
   - doesn't redirect to another domain, and isn't a "domain for sale" or parked page, **and**
   - shows the company name in its title or site name, **or** mentions the name **plus** the Form D city or phone number.
   - If the site is behind a Cloudflare challenge, it's accepted only for an exact `name.com` match on a distinctive name (8+ characters).
3. **Search results** for `"<name>" <city> <state>`, skipping directories and social sites (LinkedIn, Crunchbase, ZoomInfo, Bloomberg, and so on).

### Crunchbase profile (needs a search API key)

File: [enrich/crunchbase.py](enrich/crunchbase.py)

- **crunchbase.com is never fetched**, because it's protected by Cloudflare and its terms forbid scraping.
- It searches for `site:crunchbase.com/organization "<name>"` and takes the profile URL from the results.
- **Strict matching:** name similarity must be ≥ 0.9. For example, "Inside Reality" vs "Unseen Reality" scores 0.79 and is rejected.
- The **summary** is the company's own description, taken from the search result and trimmed to about 300 characters.
- Same-name companies can't be told apart by name alone, so check the summary for obvious mismatches.

### Executive LinkedIn profiles (needs a search API key)

- Takes up to **3 people** marked "Executive Officer" on the Form D filing.
- Searches for `site:linkedin.com/in "<full name>" "<company>"` and accepts the first profile URL whose title or URL contains the person's last name.
- **Profiles are never opened**; only the URL from the search result is stored.

### Search engines

File: [enrich/search.py](enrich/search.py)

Search is needed for Crunchbase, executives, and the fallback steps of LinkedIn and website. Engines are tried in this order, using whichever have keys:

| Engine | Key variable | Free allowance |
|---|---|---|
| Tavily | `TAVILY_API_KEY` | 1,000 searches a month |
| SerpAPI | `SERPAPI_API_KEY` | 250 searches a month |
| Serper | `SERPER_API_KEY` | 2,500 searches, one-time |
| Brave | `BRAVE_API_KEY` | About $5 of monthly credit |
| DuckDuckGo Lite | none | Free, but shows a captcha after a few queries |

If an engine fails (bad key, quota used up, captcha), the next one is tried. DuckDuckGo is paused for 30 minutes after a captcha. If **every** engine fails, the step is skipped and not marked as done, so it's retried on the next run. A genuine "no results" answer *is* cached. Bing is deliberately not used: it returns unrelated junk results to scripts.

---

## 7. Step 4: Results and export

**Results table columns:** Company (entity type, incorporation year, LinkedIn size), Location, Industry and security types, Offering, Sold (with a bar showing % sold), Investors, First sale and filed date, Contact (phone, website), Executives (with LinkedIn links where found), and Links (website, LinkedIn, Crunchbase, SEC filing).

**Export** (CSV opens cleanly in Excel; the Excel file has a frozen header row). Columns:

`Company, CIK, Street, City, State, Zip, Phone, Industry, Entity type, Incorporated, Revenue range, Securities, Total offering, Total sold, Sold %, Investors, First sale, Filed, Form, Website, LinkedIn, LinkedIn industry, Company size, LinkedIn followers, Crunchbase, Crunchbase summary, Executives, Executive LinkedIn, SEC filing`

---

## 8. Background jobs

File: [jobs.py](jobs.py)

Searches and data loads run as **background jobs**, so the web page never freezes.

- Up to **2 jobs run at once**; more wait in a queue.
- Each job's status, progress and message are stored in the `jobs` table. The job and results pages poll `/api/jobs/<id>`.
- A search job:
  1. runs the filter query and saves the matching companies (results are available immediately), then
  2. enriches the top N one at a time, updating progress after each company.
- If one company's enrichment fails, the error is noted and the job continues.
- The final message reports how many companies were only partly enriched (for example because LinkedIn was rate-limited). Run the same search again later to fill the gaps.
- Jobs still running when the server stops are marked **"Interrupted"** on the next start.

---

## 9. Caching and re-runs

| What | Cached for | Why |
|---|---|---|
| Company enrichment (`enrichment` table) | 30 days (`ENRICH_CACHE_DAYS`) | Re-running a search doesn't repeat finished lookups |
| Executive profile links (`person_links`) | Indefinitely | One search per person |
| Search engine results (`search_cache`) | 14 days | Saves your search API quota |

The cache remembers *which steps* were completed for each company. If you first enrich with Website + LinkedIn and later add Crunchbase, only the Crunchbase step runs. Steps that were blocked are not recorded as done, so they're retried automatically.

**Search API budget:** with every option on, a company uses about 1 Crunchbase search plus 1 per executive (up to 3), so about **4 searches**. LinkedIn and website usually need none. On Tavily's free 1,000 searches a month, that's roughly **250 fully enriched companies a month**.

---

## 10. Configuration (.env)

All settings live in [config.py](config.py) and can be set in `.env` or as environment variables. Real environment variables take precedence over `.env`. `.env` is listed in `.gitignore`, so keys aren't committed.

| Variable | Default | Purpose |
|---|---|---|
| `SEC_USER_AGENT` | placeholder | **Required.** Name + email sent to the SEC |
| `SEC_MAX_RPS` | `8` | SEC requests per second (SEC limit is 10) |
| `TAVILY_API_KEY` | – | Tavily search key |
| `SERPAPI_API_KEY` | – | SerpAPI key |
| `SERPER_API_KEY` | – | Serper key |
| `BRAVE_API_KEY` | – | Brave Search API key |
| `LINKEDIN_DELAY_MIN` / `LINKEDIN_DELAY_MAX` | `8` / `15` | Seconds between LinkedIn requests |
| `LINKEDIN_COOLDOWN_MIN` | `15` | Minutes to pause LinkedIn after a block |
| `DDG_DELAY_MIN` / `DDG_DELAY_MAX` | `8` / `15` | Seconds between DuckDuckGo searches |
| `DDG_COOLDOWN_MIN` | `30` | Minutes to pause DuckDuckGo after a captcha |
| `HTTP_PROXY_URL` | – | Proxy for enrichment requests, e.g. `http://user:pass@host:port`. Rotating proxies raise the LinkedIn limit |
| `BROWSER_USER_AGENT` | Chrome UA | Browser identity for websites and LinkedIn |
| `ENRICH_CACHE_DAYS` | `30` | How long enrichment is reused |
| `DATA_DIR` | `./data` | Where the database and raw downloads are stored |
| `DB_PATH` | `./data/leads.db` | SQLite database file |

Restart the app after changing `.env`.

---

## 11. Command line and scheduling

File: [cli.py](cli.py)

```powershell
.venv\Scripts\python cli.py quarters                       # list quarters available on sec.gov
.venv\Scripts\python cli.py load-quarter 2026q3 2026q2     # load quarterly data sets
.venv\Scripts\python cli.py fetch-daily --days 3           # fetch recent filings
```

**Keep data fresh automatically** (Windows Task Scheduler, run daily, e.g. 7:00 AM):

```powershell
schtasks /create /tn "LeadFinder daily" /sc daily /st 07:00 `
  /tr "D:\scraping\.venv\Scripts\python.exe D:\scraping\cli.py fetch-daily --days 3"
```

`--days 3` overlaps the previous runs, so a missed day or weekend is covered. Filings already stored are skipped.

---

## 12. Project structure

```
scraping/
├── app.py                  Flask web app: routes, results, CSV/Excel export
├── cli.py                  Command-line data loading (for scheduling)
├── config.py               Settings + .env loader
├── db.py                   SQLite schema and helpers
├── filters.py              Search parameters → SQL query; SEC value lists
├── jobs.py                 Background job runner (thread pool)
├── edgar/
│   ├── common.py           Rate-limited SEC client, value parsers
│   ├── bulk_loader.py      Quarterly Form D data sets
│   └── daily_fetcher.py    Daily index + primary_doc.xml parser
├── enrich/
│   ├── __init__.py         Runs enrichment steps for one company, caching
│   ├── search.py           Tavily / SerpAPI / Serper / Brave / DuckDuckGo
│   ├── linkedin.py         Company handle guessing + public page verification; people search
│   ├── website.py          Domain guessing + homepage verification
│   ├── crunchbase.py       Crunchbase URL + summary from search results
│   └── match.py            Name cleaning and fuzzy matching
├── templates/              Jinja pages (base, index, job, results, admin)
│   ├── _icons.html         Inline SVG icon set (no external icon library)
│   └── _macros.html        icon() and status badge helpers
├── static/style.css        Styles (light and dark theme follow the OS)
├── data/                   Created on first run: leads.db, raw/ ZIPs
├── .env                    Your keys and settings (not committed)
└── requirements.txt        flask, httpx, beautifulsoup4, openpyxl
```

---

## 13. Database

SQLite file at `data/leads.db` (WAL mode, so the UI can read while jobs write).

| Table | Contents |
|---|---|
| `filings` | One row per Form D filing (key: accession number). All issuer and offering fields |
| `persons` | Related persons per filing: name, city/state, roles |
| `enrichment` | One row per company (CIK): website, LinkedIn fields, Crunchbase URL/summary, completed steps, timestamp |
| `person_links` | Executive LinkedIn URL per company + person |
| `search_cache` | Raw search engine results per query |
| `loaded_sources` | Which quarterly data sets have been loaded |
| `jobs` / `job_results` | Background jobs and the ranked companies each search returned |

To start fresh, stop the app and delete the `data/` folder. To redo enrichment only, empty the `enrichment`, `person_links` and `search_cache` tables.

---

## 14. Limits, costs and legal notes

**What works without any key or payment:**
- All SEC data, filters, phone numbers, addresses and executive names.
- Website discovery (worked for most companies in testing).
- LinkedIn company pages, at low volume (see below).

**Known limits:**
- **LinkedIn volume.** From one IP, logged-out LinkedIn allows only a handful of page views before returning 999. Expect a few LinkedIn matches per run. Re-runs fill in more over time, and rotating proxies (`HTTP_PROXY_URL`, paid) are needed for volume.
- **Crunchbase coverage.** Only companies that already have a Crunchbase profile can be found. Very new companies often don't.
- **Same-name companies.** Crunchbase and executive matches rely on names and can't always be verified by location. Check before outreach.
- **Form D scope.** Only US private raises that use Regulation D. Some companies file late or not at all, and Form D has no website or email fields.
- **Incorporation year** is exact only for companies under 5 years old.
- **Search APIs** have monthly free limits (see [section 9](#9-caching-and-re-runs)).

**Legal:**
- SEC EDGAR data is public and free to use. Follow the SEC's fair-access rules (identify yourself; stay under 10 requests/second). The app does both.
- LinkedIn's and Crunchbase's terms of service forbid scraping. Lead Finder **never logs in**, **never fetches crunchbase.com**, **never opens personal LinkedIn profiles**, and keeps LinkedIn requests slow. Even so, keep volumes modest and check with your own legal counsel before commercial use.
- When contacting leads, follow applicable outreach laws (for example CAN-SPAM for email and TCPA for phone calls in the US).

---

## 15. Troubleshooting

| Problem | Cause / fix |
|---|---|
| "No SEC data loaded yet" | Load a quarterly data set and fetch daily filings on **Data & jobs**. |
| "Couldn't reach the SEC listing page" | No internet, or `SEC_USER_AGENT` missing. Set it in `.env` and restart. |
| SEC requests return 403 | The SEC is blocking an anonymous or placeholder user agent. Use a real name and email. |
| Few or no results | Optional filters are too strict. Revenue range and min sold ratio remove the most. Also check the data covers your date filters (coverage is shown on both pages). |
| Job ends with "partly enriched (LinkedIn returned 999...)" | Normal LinkedIn rate limiting. Run the same search again in 15+ minutes; finished companies are skipped. |
| "all search engines are blocked or rate-limited" | No search API key, or the quota is used up. Add or check `TAVILY_API_KEY`. |
| Crunchbase or executives always empty | No search key set. The search page shows the active engines under Enrichment. |
| A job is stuck on "Interrupted" | The server was restarted mid-job. Start the search again. |
| Changes to `.env` have no effect | Restart `app.py`. |
