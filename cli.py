"""Command-line entry points, e.g. for Windows Task Scheduler / cron.

    python cli.py quarters                 # list quarterly data sets available on sec.gov
    python cli.py load-quarter 2026q3 2026q2
    python cli.py fetch-daily --days 3     # run daily to keep data fresh
"""
import argparse

import db
from edgar import bulk_loader, daily_fetcher


def _print_progress(progress, total, message):
    print(f"[{progress}/{total}] {message}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("quarters")
    lq = sub.add_parser("load-quarter")
    lq.add_argument("quarters", nargs="+")
    fd = sub.add_parser("fetch-daily")
    fd.add_argument("--days", type=int, default=3)
    args = parser.parse_args()

    db.init_db()
    if args.cmd == "quarters":
        for q in bulk_loader.list_quarters():
            print(q)
    elif args.cmd == "load-quarter":
        available = bulk_loader.list_quarters()
        for q in args.quarters:
            bulk_loader.load_quarter(q, available.get(q.lower()), _print_progress)
    elif args.cmd == "fetch-daily":
        daily_fetcher.fetch_days(args.days, progress=_print_progress)


if __name__ == "__main__":
    main()
