"""Background jobs (search + enrichment, data loads) run in a small thread pool; state lives in SQLite."""
import json
import traceback
from concurrent.futures import ThreadPoolExecutor

import db
from edgar import bulk_loader, daily_fetcher
from enrich import enrich_company
from filters import SearchParams, build_query

_pool = ThreadPoolExecutor(max_workers=2)


def update(job_id: int, **fields):
    conn = db.get_conn()
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*fields.values(), job_id])


def _progress(job_id):
    def cb(progress, total, message):
        update(job_id, progress=progress, total=total, message=message)
    return cb


def submit(kind: str, params: dict) -> int:
    conn = db.get_conn()
    with conn:
        job_id = conn.execute("INSERT INTO jobs(kind, params, status, message) VALUES (?,?,?,?)",
                              (kind, json.dumps(params), "queued", "Waiting to start")).lastrowid
    _pool.submit(_run, job_id, kind, params)
    return job_id


def _run(job_id: int, kind: str, params: dict):
    update(job_id, status="running", message="Starting")
    try:
        message = RUNNERS[kind](job_id, params)
        update(job_id, status="done", message=message, finished_at=_now())
    except Exception as exc:  # surface any failure in the UI
        traceback.print_exc()
        update(job_id, status="error", message=f"{type(exc).__name__}: {exc}", finished_at=_now())


def _now():
    return db.get_conn().execute("SELECT datetime('now')").fetchone()[0]


def _run_search(job_id: int, params: dict) -> str:
    conn = db.get_conn()
    search = SearchParams(**params["filters"])
    sql, args = build_query(search)
    accessions = [r[0] for r in conn.execute(sql, args).fetchall()]
    with conn:
        conn.executemany("INSERT INTO job_results(job_id, rank, accession) VALUES (?,?,?)",
                         [(job_id, i, a) for i, a in enumerate(accessions)])

    options = set(params.get("enrich") or [])
    if not options or not accessions:
        return f"Found {len(accessions)} companies"

    to_enrich = accessions[: params.get("max_enrich", 50)]
    incomplete, last_problem = 0, ""
    for i, acc in enumerate(to_enrich):
        filing = conn.execute("SELECT * FROM filings WHERE accession=?", (acc,)).fetchone()
        note = f" (last issue: {last_problem})" if last_problem else ""
        update(job_id, progress=i, total=len(to_enrich),
               message=f"Enriching {i + 1}/{len(to_enrich)}: {filing['entity_name']}{note}")
        try:
            problems = enrich_company(filing, options)
        except Exception as exc:  # one bad company shouldn't kill the job
            problems = [f"{type(exc).__name__}: {exc}"]
        if problems:
            incomplete += 1
            last_problem = problems[-1]
    msg = f"Found {len(accessions)} companies, enriched {len(to_enrich)}"
    if incomplete:
        msg += (f". {incomplete} were only partly enriched ({last_problem}). "
                "Run the same search again later to fill the gaps; finished lookups are cached.")
    return msg


def _run_load_quarter(job_id: int, params: dict) -> str:
    quarters = params["quarters"]
    available = bulk_loader.list_quarters()
    total = 0
    for i, q in enumerate(quarters, start=1):
        def cb(p, t, msg, i=i):
            update(job_id, progress=p, total=t, message=f"[{i}/{len(quarters)}] {msg}")
        total += bulk_loader.load_quarter(q, available.get(q), cb)
    return f"Loaded {total} filings from {', '.join(quarters)}"


def _run_fetch_daily(job_id: int, params: dict) -> str:
    saved = daily_fetcher.fetch_days(int(params.get("days", 7)), progress=_progress(job_id))
    return f"Saved {saved} new filings from the last {params.get('days', 7)} days"


RUNNERS = {"search": _run_search, "load_quarter": _run_load_quarter, "fetch_daily": _run_fetch_daily}


def mark_interrupted():
    """Jobs left running when the server stopped can never finish."""
    conn = db.get_conn()
    with conn:
        conn.execute("UPDATE jobs SET status='error', message='Interrupted (server restarted)' "
                     "WHERE status IN ('queued','running')")
