"""Fetch recent Form D filings from the EDGAR daily index and parse each primary_doc.xml."""
import xml.etree.ElementTree as ET
from datetime import date, timedelta

import db
from edgar.common import parse_amount, parse_bool, parse_date, parse_int, sec_get

DAILY_INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{d}.idx"
FORM_TYPES = {"D", "D/A"}


def list_daily_filings(day: date) -> list[dict] | None:
    """Return Form D entries for one day, or None if no index exists (weekend/holiday/not yet published)."""
    url = DAILY_INDEX_URL.format(y=day.year, q=(day.month - 1) // 3 + 1, d=day.strftime("%Y%m%d"))
    resp = sec_get(url)
    if resp.status_code == 404 or resp.status_code == 403:
        return None
    resp.raise_for_status()
    entries = []
    for line in resp.text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] not in FORM_TYPES or not parts[-1].startswith("edgar/data/"):
            continue
        filename = parts[-1]
        accession = filename.rsplit("/", 1)[-1].removesuffix(".txt")
        entries.append({
            "form": parts[0],
            "cik": parts[-3].lstrip("0"),
            "filing_date": parse_date(parts[-2]),
            "accession": accession,
        })
    return entries


def _strip_ns(root: ET.Element):
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def _t(node: ET.Element | None, path: str):
    if node is None:
        return None
    el = node.find(path)
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def parse_primary_doc(xml_text: str, accession: str, filing_date: str | None, form: str):
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    _strip_ns(root)
    iss = root.find("primaryIssuer")
    off = root.find("offeringData")
    if iss is None or off is None:
        return None, []

    year = iss.find("yearOfInc")
    inc_year = parse_int(_t(year, "value"))
    inc_over_five = 1 if parse_bool(_t(year, "overFiveYears")) else 0

    sec_types = off.find("typesOfSecuritiesOffered")
    amounts = off.find("offeringSalesAmounts")
    total_offering, indefinite = parse_amount(_t(amounts, "totalOfferingAmount"))
    total_sold, _ = parse_amount(_t(amounts, "totalAmountSold"))
    total_remaining, _ = parse_amount(_t(amounts, "totalRemaining"))
    min_inv, _ = parse_amount(_t(off, "minimumInvestmentAccepted"))

    filing = {
        "accession": accession,
        "cik": (_t(iss, "cik") or "").lstrip("0"),
        "entity_name": _t(iss, "entityName"),
        "street1": _t(iss, "issuerAddress/street1"),
        "street2": _t(iss, "issuerAddress/street2"),
        "city": _t(iss, "issuerAddress/city"),
        "state": _t(iss, "issuerAddress/stateOrCountry"),
        "zip": _t(iss, "issuerAddress/zipCode"),
        "phone": _t(iss, "issuerPhoneNumber"),
        "jurisdiction": _t(iss, "jurisdictionOfInc"),
        "entity_type": _t(iss, "entityType"),
        "inc_year": inc_year,
        "inc_over_five": inc_over_five,
        "industry": _t(off, "industryGroup/industryGroupType"),
        "fund_type": _t(off, "industryGroup/investmentFundInfo/investmentFundType"),
        "revenue_range": _t(off, "issuerSize/revenueRange") or _t(off, "issuerSize/aggregateNetAssetValueRange"),
        "is_amendment": parse_bool(_t(off, "typeOfFiling/newOrAmendment/isAmendment")),
        "sale_date": parse_date(_t(off, "typeOfFiling/dateOfFirstSale/value")),
        "yet_to_occur": parse_bool(_t(off, "typeOfFiling/dateOfFirstSale/yetToOccur")),
        "is_equity": parse_bool(_t(sec_types, "isEquityType")),
        "is_debt": parse_bool(_t(sec_types, "isDebtType")),
        "is_option": parse_bool(_t(sec_types, "isOptionToAcquireType")),
        "is_security_to_acquire": parse_bool(_t(sec_types, "isSecurityToBeAcquiredType")),
        "is_pooled": parse_bool(_t(sec_types, "isPooledInvestmentFundType")),
        "is_tenant_in_common": parse_bool(_t(sec_types, "isTenantInCommonType")),
        "is_mineral": parse_bool(_t(sec_types, "isMineralPropertyType")),
        "is_other": parse_bool(_t(sec_types, "isOtherType")),
        "other_desc": _t(sec_types, "descriptionOfOtherType"),
        "total_offering": total_offering,
        "offering_indefinite": 1 if indefinite else 0,
        "total_sold": total_sold,
        "total_remaining": total_remaining,
        "num_investors": parse_int(_t(off, "investors/totalNumberAlreadyInvested")),
        "min_investment": min_inv,
        "filing_date": filing_date,
        "submission_type": _t(root, "submissionType") or form,
        "source": "daily",
    }

    persons = []
    for i, p in enumerate(root.findall("relatedPersonsList/relatedPersonInfo"), start=1):
        rels = [r.text.strip() for r in p.findall("relatedPersonRelationshipList/relationship") if r.text]
        persons.append({
            "accession": accession,
            "seq": i,
            "first_name": _t(p, "relatedPersonName/firstName"),
            "middle_name": _t(p, "relatedPersonName/middleName"),
            "last_name": _t(p, "relatedPersonName/lastName"),
            "city": _t(p, "relatedPersonAddress/city"),
            "state": _t(p, "relatedPersonAddress/stateOrCountry"),
            "relationships": ", ".join(rels),
            "clarification": _t(p, "relationshipClarification"),
        })
    return filing, persons


def fetch_days(days: int = 7, end: date | None = None, progress=lambda *a: None) -> int:
    """Fetch Form D filings for the last `days` calendar days (skips accessions already stored)."""
    end = end or date.today()
    conn = db.get_conn()
    day_list = [end - timedelta(days=i) for i in range(days)]

    progress(0, 1, "Reading daily indexes")
    todo, seen = [], set()
    for d in day_list:
        entries = list_daily_filings(d)
        if entries is None:
            continue
        for e in entries:
            if e["accession"] in seen:
                continue
            seen.add(e["accession"])
            if not conn.execute("SELECT 1 FROM filings WHERE accession=?", (e["accession"],)).fetchone():
                todo.append(e)

    saved = 0
    for i, e in enumerate(todo, start=1):
        progress(i - 1, len(todo), f"Fetching filing {i}/{len(todo)}: {e['accession']}")
        url = f"https://www.sec.gov/Archives/edgar/data/{e['cik']}/{e['accession'].replace('-', '')}/primary_doc.xml"
        resp = sec_get(url)
        if resp.status_code != 200:
            continue
        try:
            filing, persons = parse_primary_doc(resp.text, e["accession"], e["filing_date"], e["form"])
        except ET.ParseError:
            continue
        if filing is None:
            continue
        with conn:
            db.upsert_filings(conn, [filing], persons)
        saved += 1

    progress(len(todo), max(len(todo), 1), f"Saved {saved} new filings from the last {days} days")
    return saved
