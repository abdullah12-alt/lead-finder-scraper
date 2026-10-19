"""Load SEC quarterly Form D data sets (ZIP of TSV tables) into SQLite."""
import csv
import io
import re
import sys
import zipfile

import config
import db
from edgar.common import parse_amount, parse_bool, parse_date, parse_int, sec_get

LISTING_URL = "https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets"

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def list_quarters() -> dict[str, str]:
    """Return {"2026q3": absolute_zip_url, ...}, newest first."""
    html = sec_get(LISTING_URL).text
    found = {}
    for href in re.findall(r'href="([^"]*?(\d{4}q[1-4])_d\.zip)"', html, flags=re.I):
        url, quarter = href
        if url.startswith("/"):
            url = "https://www.sec.gov" + url
        found.setdefault(quarter.lower(), url)
    return dict(sorted(found.items(), reverse=True))


def _read_tsv(zf: zipfile.ZipFile, table: str):
    name = next((n for n in zf.namelist() if n.upper().endswith("/" + table) or n.upper() == table), None)
    if name is None:
        return []
    with zf.open(name) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
        return list(csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE))


def _clean(v):
    v = (v or "").strip()
    return v or None


def load_quarter(quarter: str, url: str | None = None, progress=lambda *a: None) -> int:
    quarter = quarter.lower()
    if url is None:
        url = list_quarters().get(quarter)
        if not url:
            raise ValueError(f"Quarter {quarter} not found on SEC listing page")

    progress(0, 4, f"Downloading {quarter} data set")
    resp = sec_get(url)
    resp.raise_for_status()
    raw_dir = config.DATA_DIR / "raw"
    raw_dir.mkdir(exist_ok=True)
    (raw_dir / f"{quarter}_d.zip").write_bytes(resp.content)
    zf = zipfile.ZipFile(io.BytesIO(resp.content))

    progress(1, 4, "Reading tables")
    submissions = {r["ACCESSIONNUMBER"]: r for r in _read_tsv(zf, "FORMDSUBMISSION.TSV")}
    offerings = {r["ACCESSIONNUMBER"]: r for r in _read_tsv(zf, "OFFERING.TSV")}
    issuers = {}
    for r in _read_tsv(zf, "ISSUERS.TSV"):
        if (r.get("IS_PRIMARYISSUER_FLAG") or "").upper() == "YES":
            issuers[r["ACCESSIONNUMBER"]] = r
    people_rows = _read_tsv(zf, "RELATEDPERSONS.TSV")

    progress(2, 4, "Normalising filings")
    filings = []
    for acc, iss in issuers.items():
        off = offerings.get(acc)
        sub = submissions.get(acc, {})
        if off is None or (sub.get("TESTORLIVE") or "LIVE").upper() != "LIVE":
            continue
        total_offering, indefinite = parse_amount(off.get("TOTALOFFERINGAMOUNT"))
        total_sold, _ = parse_amount(off.get("TOTALAMOUNTSOLD"))
        total_remaining, _ = parse_amount(off.get("TOTALREMAINING"))
        min_inv, _ = parse_amount(off.get("MINIMUMINVESTMENTACCEPTED"))
        timespan = (iss.get("YEAROFINC_TIMESPAN_CHOICE") or "").strip()
        filings.append({
            "accession": acc,
            "cik": (iss.get("CIK") or "").lstrip("0") or iss.get("CIK"),
            "entity_name": _clean(iss.get("ENTITYNAME")),
            "street1": _clean(iss.get("STREET1")),
            "street2": _clean(iss.get("STREET2")),
            "city": _clean(iss.get("CITY")),
            "state": _clean(iss.get("STATEORCOUNTRY")),
            "zip": _clean(iss.get("ZIPCODE")),
            "phone": _clean(iss.get("ISSUERPHONENUMBER")),
            "jurisdiction": _clean(iss.get("JURISDICTIONOFINC")),
            "entity_type": _clean(iss.get("ENTITYTYPE")),
            "inc_year": parse_int(iss.get("YEAROFINC_VALUE_ENTERED")),
            "inc_over_five": 1 if timespan == "overFiveYears" else 0,
            "industry": _clean(off.get("INDUSTRYGROUPTYPE")),
            "fund_type": _clean(off.get("INVESTMENTFUNDTYPE")),
            "revenue_range": _clean(off.get("REVENUERANGE")) or _clean(off.get("AGGREGATENETASSETVALUERANGE")),
            "is_amendment": parse_bool(off.get("ISAMENDMENT")),
            "sale_date": parse_date(off.get("SALE_DATE")),
            "yet_to_occur": parse_bool(off.get("YETTOOCCUR")),
            "is_equity": parse_bool(off.get("ISEQUITYTYPE")),
            "is_debt": parse_bool(off.get("ISDEBTTYPE")),
            "is_option": parse_bool(off.get("ISOPTIONTOACQUIRETYPE")),
            "is_security_to_acquire": parse_bool(off.get("ISSECURITYTOBEACQUIREDTYPE")),
            "is_pooled": parse_bool(off.get("ISPOOLEDINVESTMENTFUNDTYPE")),
            "is_tenant_in_common": parse_bool(off.get("ISTENANTINCOMMONTYPE")),
            "is_mineral": parse_bool(off.get("ISMINERALPROPERTYTYPE")),
            "is_other": parse_bool(off.get("ISOTHERTYPE")),
            "other_desc": _clean(off.get("DESCRIPTIONOFOTHERTYPE")),
            "total_offering": total_offering,
            "offering_indefinite": 1 if indefinite else 0,
            "total_sold": total_sold,
            "total_remaining": total_remaining,
            "num_investors": parse_int(off.get("TOTALNUMBERALREADYINVESTED")),
            "min_investment": min_inv,
            "filing_date": parse_date(sub.get("FILING_DATE")),
            "submission_type": _clean(sub.get("SUBMISSIONTYPE")),
            "source": f"quarter:{quarter}",
        })

    valid = {f["accession"] for f in filings}
    persons = []
    for r in people_rows:
        if r["ACCESSIONNUMBER"] not in valid:
            continue
        rels = [r.get(k, "").strip() for k in ("RELATIONSHIP_1", "RELATIONSHIP_2", "RELATIONSHIP_3")]
        persons.append({
            "accession": r["ACCESSIONNUMBER"],
            "seq": parse_int(r.get("RELATEDPERSON_SEQ_KEY")) or 0,
            "first_name": _clean(r.get("FIRSTNAME")),
            "middle_name": _clean(r.get("MIDDLENAME")),
            "last_name": _clean(r.get("LASTNAME")),
            "city": _clean(r.get("CITY")),
            "state": _clean(r.get("STATEORCOUNTRY")),
            "relationships": ", ".join(x for x in rels if x),
            "clarification": _clean(r.get("RELATIONSHIPCLARIFICATION")),
        })

    progress(3, 4, f"Saving {len(filings)} filings")
    conn = db.get_conn()
    with conn:
        db.upsert_filings(conn, filings, persons)
        db.mark_loaded(conn, f"quarter:{quarter}", len(filings))
    progress(4, 4, f"Loaded {len(filings)} filings from {quarter}")
    return len(filings)
