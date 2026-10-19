"""Flask UI for Lead Finder."""
import csv
import io
import json
import time

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for

import db
import jobs
from edgar import bulk_loader
from enrich import OPTIONS as ENRICH_OPTIONS
from enrich.search import available_engines
from filters import ENTITY_TYPES, INDUSTRY_GROUPS, REVENUE_RANGES, SECURITY_TYPES, SearchParams

app = Flask(__name__)
db.init_db()
jobs.mark_interrupted()

_quarters_cache: dict = {"at": 0.0, "data": {}}


def available_quarters() -> dict[str, str]:
    if time.time() - _quarters_cache["at"] > 3600 or not _quarters_cache["data"]:
        try:
            _quarters_cache["data"] = bulk_loader.list_quarters()
            _quarters_cache["at"] = time.time()
        except Exception as exc:  # SEC unreachable: show the error on the page rather than 500
            app.logger.warning("Could not list quarters: %s", exc)
    return _quarters_cache["data"]


def coverage():
    conn = db.get_conn()
    row = conn.execute("SELECT COUNT(*) n, COUNT(DISTINCT cik) companies, MIN(filing_date) first, "
                       "MAX(filing_date) last FROM filings").fetchone()
    return dict(row)


@app.context_processor
def recent_searches():
    """Last few searches for the sidebar."""
    items = []
    for r in db.get_conn().execute("SELECT j.id, j.params, j.status, COUNT(jr.rank) n FROM jobs j "
                                   "LEFT JOIN job_results jr ON jr.job_id = j.id WHERE j.kind='search' "
                                   "GROUP BY j.id ORDER BY j.id DESC LIMIT 6"):
        industries = json.loads(r["params"] or "{}").get("filters", {}).get("industries", [])
        label = ", ".join(industries[:2]) + (f" +{len(industries) - 2}" if len(industries) > 2 else "")
        items.append({"id": r["id"], "label": label or "Search", "count": r["n"], "status": r["status"]})
    return {"recent_searches": items}


@app.route("/")
def index():
    return render_template("index.html", industry_groups=INDUSTRY_GROUPS, entity_types=ENTITY_TYPES,
                           revenue_ranges=REVENUE_RANGES, security_types=SECURITY_TYPES,
                           enrich_options=ENRICH_OPTIONS, engines=available_engines(), coverage=coverage())


@app.post("/search")
def search():
    params = SearchParams.from_form(request.form)
    if not params.industries:
        return render_template("index.html", industry_groups=INDUSTRY_GROUPS, entity_types=ENTITY_TYPES,
                               revenue_ranges=REVENUE_RANGES, security_types=SECURITY_TYPES,
                               enrich_options=ENRICH_OPTIONS, engines=available_engines(), coverage=coverage(),
                               error="Pick at least one industry."), 400
    enrich = [o for o in request.form.getlist("enrich") if o in ENRICH_OPTIONS]
    try:
        max_enrich = max(1, min(int(request.form.get("max_enrich") or 50), 1000))
    except ValueError:
        max_enrich = 50
    job_id = jobs.submit("search", {"filters": params.to_dict(), "enrich": enrich, "max_enrich": max_enrich})
    return redirect(url_for("job_page", job_id=job_id))


def get_job(job_id: int):
    row = db.get_conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        abort(404)
    job = dict(row)
    job["params"] = json.loads(job["params"] or "{}")
    return job


@app.route("/jobs/<int:job_id>")
def job_page(job_id):
    return render_template("job.html", job=get_job(job_id))


@app.route("/api/jobs/<int:job_id>")
def job_status(job_id):
    job = get_job(job_id)
    return jsonify({k: job[k] for k in ("id", "kind", "status", "progress", "total", "message")})


def get_results(job_id: int) -> list[dict]:
    conn = db.get_conn()
    rows = [dict(r) for r in conn.execute("""
        SELECT f.*, e.website, e.linkedin_url, e.linkedin_industry, e.linkedin_size, e.linkedin_employees,
               e.linkedin_followers, e.linkedin_founded, e.linkedin_hq, e.crunchbase_url, e.crunchbase_snippet
        FROM job_results r
        JOIN filings f ON f.accession = r.accession
        LEFT JOIN enrichment e ON e.cik = f.cik
        WHERE r.job_id = ? ORDER BY r.rank""", (job_id,))]
    for r in rows:
        people = conn.execute("""
            SELECT p.first_name, p.last_name, p.relationships, p.clarification, pl.linkedin_url
            FROM persons p
            LEFT JOIN person_links pl ON pl.cik = ? AND pl.full_name = TRIM(COALESCE(p.first_name,'') || ' ' || COALESCE(p.last_name,''))
            WHERE p.accession = ? ORDER BY p.seq""", (r["cik"], r["accession"])).fetchall()
        r["people"] = [{"name": " ".join(x for x in (p["first_name"], p["last_name"]) if x),
                        "role": p["clarification"] or p["relationships"], "linkedin": p["linkedin_url"]}
                       for p in people]
        r["sold_pct"] = (round(100 * r["total_sold"] / r["total_offering"])
                         if r["total_offering"] and r["total_sold"] is not None else None)
        r["securities"] = ", ".join(label for key, (label, col) in SECURITY_TYPES.items() if r.get(col))
        r["edgar_url"] = (f"https://www.sec.gov/Archives/edgar/data/{r['cik']}/"
                          f"{r['accession'].replace('-', '')}/{r['accession']}-index.htm")
    return rows


@app.route("/results/<int:job_id>")
def results(job_id):
    job = get_job(job_id)
    rows = get_results(job_id)
    offerings = sorted(r["total_offering"] for r in rows if r["total_offering"])
    stats = {
        "count": len(rows),
        "total_sold": sum(r["total_sold"] or 0 for r in rows),
        "median_offering": offerings[len(offerings) // 2] if offerings else None,
        "with_website": sum(1 for r in rows if r["website"]),
        "with_linkedin": sum(1 for r in rows if r["linkedin_url"]),
        "with_people": sum(1 for r in rows if r["people"]),
    }
    return render_template("results.html", job=job, rows=rows, stats=stats)


EXPORT_COLUMNS = [
    ("Company", "entity_name"), ("CIK", "cik"), ("Street", "street1"), ("City", "city"), ("State", "state"),
    ("Zip", "zip"), ("Phone", "phone"), ("Industry", "industry"), ("Entity type", "entity_type"),
    ("Incorporated", "inc_year"), ("Revenue range", "revenue_range"), ("Securities", "securities"),
    ("Total offering", "total_offering"), ("Total sold", "total_sold"), ("Sold %", "sold_pct"),
    ("Investors", "num_investors"), ("First sale", "sale_date"), ("Filed", "filing_date"),
    ("Form", "submission_type"), ("Website", "website"), ("LinkedIn", "linkedin_url"),
    ("LinkedIn industry", "linkedin_industry"), ("Company size", "linkedin_size"),
    ("LinkedIn followers", "linkedin_followers"), ("Crunchbase", "crunchbase_url"),
    ("Crunchbase summary", "crunchbase_snippet"), ("Executives", "people_text"),
    ("Executive LinkedIn", "people_links"), ("SEC filing", "edgar_url"),
]


def export_rows(job_id):
    for r in get_results(job_id):
        r["inc_year"] = r["inc_year"] or (">5 yrs ago" if r["inc_over_five"] else None)
        r["people_text"] = "; ".join(f"{p['name']} ({p['role']})" for p in r["people"])
        r["people_links"] = "; ".join(f"{p['name']}: {p['linkedin']}" for p in r["people"] if p["linkedin"])
        yield [r.get(key) for _, key in EXPORT_COLUMNS]


@app.route("/results/<int:job_id>/export.csv")
def export_csv(job_id):
    get_job(job_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([h for h, _ in EXPORT_COLUMNS])
    writer.writerows(export_rows(job_id))
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=leads_{job_id}.csv"})


@app.route("/results/<int:job_id>/export.xlsx")
def export_xlsx(job_id):
    from openpyxl import Workbook
    get_job(job_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append([h for h, _ in EXPORT_COLUMNS])
    for row in export_rows(job_id):
        ws.append(row)
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=leads_{job_id}.xlsx"})


@app.route("/admin")
def admin():
    conn = db.get_conn()
    loaded = {r["name"].removeprefix("quarter:"): r["rows"]
              for r in conn.execute("SELECT * FROM loaded_sources WHERE name LIKE 'quarter:%'")}
    recent_jobs = [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 25")]
    return render_template("admin.html", quarters=available_quarters(), loaded=loaded,
                           jobs=recent_jobs, coverage=coverage())


@app.post("/admin/load-quarters")
def load_quarters():
    quarters = [q for q in request.form.getlist("quarters") if q in available_quarters()]
    if not quarters:
        return redirect(url_for("admin"))
    return redirect(url_for("job_page", job_id=jobs.submit("load_quarter", {"quarters": quarters})))


@app.post("/admin/fetch-daily")
def fetch_daily():
    try:
        days = max(1, min(int(request.form.get("days") or 7), 120))
    except ValueError:
        days = 7
    return redirect(url_for("job_page", job_id=jobs.submit("fetch_daily", {"days": days})))


@app.template_filter("money")
def money(v):
    if v is None:
        return "-"
    if v >= 1_000_000:
        return f"${v / 1_000_000:,.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:,.0f}K"
    return f"${v:,.0f}"


if __name__ == "__main__":
    app.run(debug=False, port=5000)
