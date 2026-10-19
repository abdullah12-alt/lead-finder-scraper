"""Translate search parameters into a SQL query over the latest Form D filing per company."""
from dataclasses import dataclass, field, asdict

# Fixed SEC Form D industry group list (Item 4), grouped as on the form.
INDUSTRY_GROUPS = {
    "Agriculture": ["Agriculture"],
    "Banking & Financial Services": ["Commercial Banking", "Insurance", "Investing", "Investment Banking",
                                     "Pooled Investment Fund", "Other Banking and Financial Services"],
    "Business Services": ["Business Services"],
    "Energy": ["Coal Mining", "Electric Utilities", "Energy Conservation", "Environmental Services",
               "Oil and Gas", "Other Energy"],
    "Health Care": ["Biotechnology", "Health Insurance", "Hospitals and Physicians", "Pharmaceuticals",
                    "Other Health Care"],
    "Manufacturing": ["Manufacturing"],
    "Real Estate": ["Commercial", "Construction", "REITS and Finance", "Residential", "Other Real Estate"],
    "Retailing": ["Retailing"],
    "Restaurants": ["Restaurants"],
    "Technology": ["Computers", "Telecommunications", "Other Technology"],
    "Travel": ["Airlines and Airports", "Lodging and Conventions", "Tourism and Travel Services", "Other Travel"],
    "Other": ["Other"],
}
ENTITY_TYPES = ["Corporation", "Limited Liability Company", "Limited Partnership", "General Partnership",
                "Business Trust", "Other"]
REVENUE_RANGES = ["No Revenues", "$1 - $1,000,000", "$1,000,001 - $5,000,000", "$5,000,001 - $25,000,000",
                  "$25,000,001 - $100,000,000", "Over $100,000,000", "Decline to Disclose", "Not Applicable"]
SECURITY_TYPES = {
    "equity": ("Equity", "is_equity"),
    "debt": ("Debt", "is_debt"),
    "option": ("Option / warrant", "is_option"),
    "security_to_acquire": ("Security to be acquired on exercise", "is_security_to_acquire"),
    "pooled": ("Pooled investment fund interests", "is_pooled"),
    "tenant_in_common": ("Tenant-in-common", "is_tenant_in_common"),
    "mineral": ("Mineral property", "is_mineral"),
    "other": ("Other", "is_other"),
}


@dataclass
class SearchParams:
    industries: list[str] = field(default_factory=list)
    exclude_funds: bool = True
    states: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    raise_basis: str = "offering"          # "offering" or "sold"
    min_raise: float | None = None
    max_raise: float | None = None
    min_sold_ratio: float | None = None    # 0..1
    inc_year_min: int | None = None
    inc_year_max: int | None = None
    entity_types: list[str] = field(default_factory=list)
    revenue_ranges: list[str] = field(default_factory=list)
    security_types: list[str] = field(default_factory=list)
    min_investors: int | None = None
    first_sale_within_days: int | None = None
    filed_within_days: int | None = None
    limit: int = 500

    @classmethod
    def from_form(cls, form) -> "SearchParams":
        def num(name, cast=float):
            v = (form.get(name) or "").strip().replace(",", "").replace("$", "")
            if not v:
                return None
            try:
                return cast(float(v))
            except ValueError:
                return None

        def csv_list(name, upper=False):
            items = [x.strip() for x in (form.get(name) or "").replace(";", ",").split(",") if x.strip()]
            return [x.upper() for x in items] if upper else items

        ratio = num("min_sold_ratio")
        return cls(
            industries=form.getlist("industries"),
            exclude_funds=form.get("exclude_funds") == "on",
            states=csv_list("states", upper=True),
            cities=csv_list("cities"),
            raise_basis="sold" if form.get("raise_basis") == "sold" else "offering",
            min_raise=num("min_raise"),
            max_raise=num("max_raise"),
            min_sold_ratio=(ratio / 100 if ratio is not None and ratio > 1 else ratio),
            inc_year_min=num("inc_year_min", int),
            inc_year_max=num("inc_year_max", int),
            entity_types=form.getlist("entity_types"),
            revenue_ranges=form.getlist("revenue_ranges"),
            security_types=[s for s in form.getlist("security_types") if s in SECURITY_TYPES],
            min_investors=num("min_investors", int),
            first_sale_within_days=num("first_sale_within_days", int),
            filed_within_days=num("filed_within_days", int),
            limit=num("limit", int) or 500,
        )

    def to_dict(self):
        return asdict(self)


def build_query(p: SearchParams) -> tuple[str, list]:
    where, args = ["rn = 1"], []

    def in_list(column, values, lower=False):
        col = f"LOWER({column})" if lower else column
        where.append(f"{col} IN ({','.join('?' * len(values))})")
        args.extend(v.lower() if lower else v for v in values)

    if p.industries:
        in_list("industry", p.industries)
    if p.exclude_funds:
        where.append("COALESCE(industry, '') <> 'Pooled Investment Fund' AND COALESCE(is_pooled, 0) = 0")
    if p.states:
        in_list("UPPER(state)", p.states)
    if p.cities:
        in_list("city", p.cities, lower=True)

    amount_col = "total_sold" if p.raise_basis == "sold" else "total_offering"
    if p.min_raise is not None:
        where.append(f"{amount_col} >= ?")
        args.append(p.min_raise)
    if p.max_raise is not None:
        where.append(f"{amount_col} <= ?")
        args.append(p.max_raise)
    if p.min_sold_ratio is not None:
        # Indefinite offerings have no total, so the ratio can't be computed and they're excluded.
        where.append("total_offering > 0 AND total_sold * 1.0 / total_offering >= ?")
        args.append(p.min_sold_ratio)

    # Form D gives an exact year only for companies incorporated within the last 5 years;
    # older ones are just flagged "over five years", which satisfies a max-year bound only.
    if p.inc_year_min is not None:
        where.append("inc_year >= ?")
        args.append(p.inc_year_min)
    if p.inc_year_max is not None:
        where.append("(inc_year <= ? OR inc_over_five = 1)")
        args.append(p.inc_year_max)

    if p.entity_types:
        in_list("entity_type", p.entity_types)
    if p.revenue_ranges:
        in_list("revenue_range", p.revenue_ranges)
    if p.security_types:
        where.append("(" + " OR ".join(f"{SECURITY_TYPES[s][1]} = 1" for s in p.security_types) + ")")
    if p.min_investors is not None:
        where.append("num_investors >= ?")
        args.append(p.min_investors)
    if p.first_sale_within_days is not None:
        where.append("sale_date >= date('now', ?)")
        args.append(f"-{p.first_sale_within_days} days")
    if p.filed_within_days is not None:
        where.append("filing_date >= date('now', ?)")
        args.append(f"-{p.filed_within_days} days")

    sql = f"""
        WITH latest AS (
            SELECT f.*, ROW_NUMBER() OVER (PARTITION BY cik ORDER BY filing_date DESC, accession DESC) AS rn
            FROM filings f
        )
        SELECT accession FROM latest
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(sale_date, filing_date) DESC, total_sold DESC
        LIMIT ?
    """
    args.append(p.limit)
    return sql, args
