"""SQLite storage. One connection per thread; WAL so the UI can read while jobs write."""
import sqlite3
import threading

import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    accession          TEXT PRIMARY KEY,
    cik                TEXT NOT NULL,
    entity_name        TEXT,
    street1            TEXT,
    street2            TEXT,
    city               TEXT,
    state              TEXT,
    zip                TEXT,
    phone              TEXT,
    jurisdiction       TEXT,
    entity_type        TEXT,
    inc_year           INTEGER,     -- only reported when incorporated within 5 years
    inc_over_five      INTEGER,     -- 1 = "over five years ago" (no exact year on Form D)
    industry           TEXT,
    fund_type          TEXT,
    revenue_range      TEXT,
    is_amendment       INTEGER,
    sale_date          TEXT,        -- ISO date of first sale
    yet_to_occur       INTEGER,
    is_equity          INTEGER,
    is_debt            INTEGER,
    is_option          INTEGER,
    is_security_to_acquire INTEGER,
    is_pooled          INTEGER,
    is_tenant_in_common INTEGER,
    is_mineral         INTEGER,
    is_other           INTEGER,
    other_desc         TEXT,
    total_offering     REAL,        -- NULL when "Indefinite"
    offering_indefinite INTEGER,
    total_sold         REAL,
    total_remaining    REAL,
    num_investors      INTEGER,
    min_investment     REAL,
    filing_date        TEXT,        -- ISO
    submission_type    TEXT,
    source             TEXT
);
CREATE INDEX IF NOT EXISTS ix_filings_cik ON filings(cik, filing_date);
CREATE INDEX IF NOT EXISTS ix_filings_industry ON filings(industry);

CREATE TABLE IF NOT EXISTS persons (
    accession     TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    first_name    TEXT,
    middle_name   TEXT,
    last_name     TEXT,
    city          TEXT,
    state         TEXT,
    relationships TEXT,
    clarification TEXT,
    PRIMARY KEY (accession, seq)
);

CREATE TABLE IF NOT EXISTS enrichment (
    cik                TEXT PRIMARY KEY,
    website            TEXT,
    linkedin_url       TEXT,
    linkedin_industry  TEXT,
    linkedin_size      TEXT,
    linkedin_employees INTEGER,
    linkedin_followers TEXT,
    linkedin_founded   TEXT,
    linkedin_hq        TEXT,
    linkedin_description TEXT,
    crunchbase_url     TEXT,
    crunchbase_snippet TEXT,
    notes              TEXT,
    updated_at         TEXT
);

CREATE TABLE IF NOT EXISTS person_links (
    cik          TEXT NOT NULL,
    full_name    TEXT NOT NULL,
    linkedin_url TEXT,
    updated_at   TEXT,
    PRIMARY KEY (cik, full_name)
);

CREATE TABLE IF NOT EXISTS search_cache (
    engine     TEXT NOT NULL,
    query      TEXT NOT NULL,
    results    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (engine, query)
);

CREATE TABLE IF NOT EXISTS loaded_sources (
    name      TEXT PRIMARY KEY,   -- e.g. "quarter:2026q3" or "daily:20261016"
    rows      INTEGER,
    loaded_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,     -- search | load_quarter | fetch_daily
    params     TEXT,
    status     TEXT NOT NULL,     -- queued | running | done | error
    progress   INTEGER DEFAULT 0,
    total      INTEGER DEFAULT 0,
    message    TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS job_results (
    job_id    INTEGER NOT NULL,
    rank      INTEGER NOT NULL,
    accession TEXT NOT NULL,
    PRIMARY KEY (job_id, rank)
);
"""


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH, timeout=60, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def init_db():
    get_conn().executescript(SCHEMA)


FILING_COLUMNS = [
    "accession", "cik", "entity_name", "street1", "street2", "city", "state", "zip", "phone",
    "jurisdiction", "entity_type", "inc_year", "inc_over_five", "industry", "fund_type",
    "revenue_range", "is_amendment", "sale_date", "yet_to_occur", "is_equity", "is_debt",
    "is_option", "is_security_to_acquire", "is_pooled", "is_tenant_in_common", "is_mineral",
    "is_other", "other_desc", "total_offering", "offering_indefinite", "total_sold",
    "total_remaining", "num_investors", "min_investment", "filing_date", "submission_type", "source",
]
PERSON_COLUMNS = ["accession", "seq", "first_name", "middle_name", "last_name", "city", "state",
                  "relationships", "clarification"]


def upsert_filings(conn, filings: list[dict], persons: list[dict]):
    fcols = ",".join(FILING_COLUMNS)
    fqs = ",".join("?" * len(FILING_COLUMNS))
    conn.executemany(f"INSERT OR REPLACE INTO filings ({fcols}) VALUES ({fqs})",
                     [tuple(f.get(c) for c in FILING_COLUMNS) for f in filings])
    pcols = ",".join(PERSON_COLUMNS)
    pqs = ",".join("?" * len(PERSON_COLUMNS))
    conn.executemany(f"INSERT OR REPLACE INTO persons ({pcols}) VALUES ({pqs})",
                     [tuple(p.get(c) for c in PERSON_COLUMNS) for p in persons])


def mark_loaded(conn, name: str, rows: int):
    conn.execute("INSERT OR REPLACE INTO loaded_sources(name, rows, loaded_at) VALUES (?,?,datetime('now'))",
                 (name, rows))


def is_loaded(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM loaded_sources WHERE name=?", (name,)).fetchone() is not None
