#!/usr/bin/env python3

import json
import time
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
USER_AGENT = "aicgbf-financial-comparison/1.0 christopher.menor2001@gmail.com"
COMPANIES = [
    ("AAPL", 320193),
    ("MSFT", 789019),
    ("NVDA", 1045810),
    ("GOOGL", 1652044),
    ("AMZN", 1018724),
]
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
ANNUAL_FORMS = {"10-K"}


def parse_date(value):
    return date.fromisoformat(value)


def annual_duration(fact):
    if not fact.get("start") or not fact.get("end"):
        return False
    return 330 <= (parse_date(fact["end"]) - parse_date(fact["start"])).days <= 400


def annual_records(company, tag, duration=True):
    records = company["facts"].get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", [])
    return [
        fact
        for fact in records
        if fact.get("form") in ANNUAL_FORMS
        and fact.get("end")
        and fact.get("filed")
        and fact.get("accn")
        and (annual_duration(fact) if duration else not fact.get("start"))
    ]

def latest_revenue(company):
    choices = []
    for priority, tag in enumerate(REVENUE_TAGS):
        records = annual_records(company, tag)
        if records:
            latest_end = max(fact["end"] for fact in records)
            choices.append((latest_end, priority, tag, records))
    if not choices:
        raise ValueError(f"No annual revenue facts found for {company.get('entityName', 'company')}")
    latest_end = max(choice[0] for choice in choices)
    _, _, tag, records = min(choice for choice in choices if choice[0] == latest_end)
    current_candidates = [fact for fact in records if fact["end"] == latest_end]
    current = max(current_candidates, key=lambda fact: (fact["filed"], fact["accn"]))
    return tag, current, records


def matching_fact(records, end, accession, start=None):
    matches = [
        fact
        for fact in records
        if fact["end"] == end
        and fact["accn"] == accession
        and (start is None or fact.get("start") == start)
    ]
    if not matches:
        return None
    return max(matches, key=lambda fact: fact["filed"])


def prior_revenue(records, current):
    same_filing = [
        fact
        for fact in records
        if fact["accn"] == current["accn"]
        and fact["end"] < current["end"]
        and fact.get("start", "") < current["start"]
    ]
    if same_filing:
        prior_end = max(fact["end"] for fact in same_filing)
        return max(
            (fact for fact in same_filing if fact["end"] == prior_end),
            key=lambda fact: fact["filed"],
        )
    earlier = [fact for fact in records if fact["end"] < current["end"]]
    if not earlier:
        return None
    prior_end = max(fact["end"] for fact in earlier)
    return max(
        (fact for fact in earlier if fact["end"] == prior_end),
        key=lambda fact: (fact["filed"], fact["accn"]),
    )


def analyze_company(ticker, cik, company):
    revenue_tag, current_revenue, revenue_records = latest_revenue(company)
    start = current_revenue["start"]
    end = current_revenue["end"]
    accession = current_revenue["accn"]
    previous_revenue = prior_revenue(revenue_records, current_revenue)
    operating_income = matching_fact(
        annual_records(company, "OperatingIncomeLoss"), end, accession, start
    )
    net_income = matching_fact(annual_records(company, "NetIncomeLoss"), end, accession, start)
    assets = matching_fact(annual_records(company, "Assets", duration=False), end, accession)

    revenue = current_revenue["val"]
    growth = None
    if previous_revenue is not None and previous_revenue["val"] != 0:
        growth = (revenue / previous_revenue["val"] - 1) * 100
    operating_margin = None
    if operating_income is not None and revenue != 0:
        operating_margin = operating_income["val"] / revenue * 100
    net_margin = None
    if net_income is not None and revenue != 0:
        net_margin = net_income["val"] / revenue * 100

    return {
        "ticker": ticker,
        "cik": cik,
        "entity": company.get("entityName", ticker),
        "fiscal_year_end": end,
        "fiscal_year_start": start,
        "revenue_tag": revenue_tag,
        "revenue": current_revenue,
        "prior_revenue": previous_revenue,
        "assets": assets,
        "operating_income": operating_income,
        "net_income": net_income,
        "growth": growth,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
    }


def load_companyfacts(ticker, cik):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"companyfacts_CIK{cik:010d}.json"
    if path.exists():
        raw = path.read_bytes()
    else:
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
        json.loads(raw)
        with path.open("xb") as output:
            output.write(raw)
        time.sleep(0.2)
    company = json.loads(raw)
    return analyze_company(ticker, cik, company)


def dollars(value):
    return "n/a" if value is None else f"${value / 1_000_000_000:.1f}B"


def percentage(value):
    return "n/a" if value is None else f"{value:.1f}%"


def print_fact(label, tag, fact, expected_end):
    if fact is None:
        print(
            f"  {label}: n/a | XBRL us-gaap:{tag} | period end: {expected_end or 'n/a'} "
            "| filing date: n/a (no matching annual 10-K fact)"
        )
        return
    period = f"{fact['start']} to {fact['end']}" if fact.get("start") else f"instant {fact['end']}"
    print(
        f"  {label}: {dollars(fact['val'])} | XBRL us-gaap:{tag} | period: {period} "
        f"| period end: {fact['end']} | filing date: {fact['filed']} | accession: {fact['accn']}"
    )


def print_report(results):
    print("\nLatest fiscal-year comparison")
    print("Source: SEC EDGAR company facts API only; dollars in $B, rounded to one decimal.")
    print("Size = revenue and assets; growth = year-over-year revenue; margins = income / revenue.")
    print()
    headers = ["Ticker", "FY end", "Revenue", "Assets", "Revenue growth", "Operating margin", "Net margin"]
    rows = [
        [
            result["ticker"],
            result["fiscal_year_end"],
            dollars(result["revenue"]["val"]),
            dollars(result["assets"]["val"] if result["assets"] else None),
            percentage(result["growth"]),
            percentage(result["operating_margin"]),
            percentage(result["net_margin"]),
        ]
        for result in results
    ]
    widths = [max(len(str(row[index])) for row in [headers] + rows) for index in range(len(headers))]
    print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))

    print("\nXBRL provenance for every input value")
    for result in results:
        print(f"\n{result['ticker']} — {result['entity']} (CIK {result['cik']:010d})")
        print(
            f"  Fiscal year: {result['fiscal_year_start']} to {result['fiscal_year_end']} "
            f"({(parse_date(result['fiscal_year_end']) - parse_date(result['fiscal_year_start'])).days + 1} days)"
        )
        print_fact("Revenue (growth input; margin denominator)", result["revenue_tag"], result["revenue"], result["fiscal_year_end"])
        print_fact("Prior-year revenue (growth denominator)", result["revenue_tag"], result["prior_revenue"], None)
        print_fact("Assets", "Assets", result["assets"], result["fiscal_year_end"])
        print_fact("Operating income (operating-margin numerator)", "OperatingIncomeLoss", result["operating_income"], result["fiscal_year_end"])
        print_fact("Net income (net-margin numerator)", "NetIncomeLoss", result["net_income"], result["fiscal_year_end"])
        derived = [
            (
                "Revenue growth",
                result["growth"],
                [result["revenue"], result["prior_revenue"]],
                "(current revenue / prior-year revenue - 1) × 100",
            ),
            (
                "Operating margin",
                result["operating_margin"],
                [result["operating_income"], result["revenue"]],
                "operating income / revenue × 100",
            ),
            (
                "Net margin",
                result["net_margin"],
                [result["net_income"], result["revenue"]],
                "net income / revenue × 100",
            ),
        ]
        for label, value, sources, formula in derived:
            period_ends = ", ".join(fact["end"] if fact else "n/a" for fact in sources)
            filing_dates = ", ".join(fact["filed"] if fact else "n/a" for fact in sources)
            print(
                f"  {label}: {percentage(value)} | XBRL: n/a (calculated: {formula}) "
                f"| source period ends: {period_ends} | source filing dates: {filing_dates}"
            )

    distinct_ends = sorted({result["fiscal_year_end"] for result in results})
    if len(distinct_ends) > 1:
        print("\n* Fiscal-year mismatch: " + "; ".join(
            f"{result['ticker']} {result['fiscal_year_end']}" for result in results
        ))
        print("  Comparisons use each firm's own latest fiscal year, so the periods are not coterminous.")
        for result in results:
            days = (parse_date(result["fiscal_year_end"]) - parse_date(result["fiscal_year_start"])).days + 1
            if days not in {365, 366}:
                print(f"  {result['ticker']} has a {days}-day fiscal year, not a 365/366-day calendar year.")


def main():
    results = [load_companyfacts(ticker, cik) for ticker, cik in COMPANIES]
    print_report(results)


if __name__ == "__main__":
    main()
