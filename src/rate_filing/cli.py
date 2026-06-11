"""CLI entrypoint: run the pipeline without Airflow.

    rate-filing run input/geico.pdf
    rate-filing run --all          # every PDF in INPUT_DIR
"""

import argparse
import sys
from pathlib import Path

from .config import Config
from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="rate-filing")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the pipeline on one or more PDFs")
    r.add_argument("pdf", nargs="*", help="path(s) to PDF(s) (default: all in INPUT_DIR)")
    r.add_argument("--all", action="store_true", help="process every PDF in INPUT_DIR")
    args = p.parse_args(argv)

    cfg = Config.from_env()
    if args.cmd == "run":
        if args.all or not args.pdf:
            pdfs = sorted(cfg.input_dir.glob("*.pdf"))
        else:
            pdfs = [Path(x) for x in args.pdf]
        if not pdfs:
            print("no PDFs to process", file=sys.stderr)
            return 1
        res = run(pdfs, cfg)          # triaged; rows + chunks stored in Postgres
        print(f"\n== bill-pay extraction (Excel disabled; data in Postgres) ==")
        print(f"  total bill-pay rows: {res.total_rows}")
        for name, n in res.per_file.items():
            print(f"    processed: {name}: {n} rows, {res.chunks_stored.get(name, 0)} chunks  "
                  f"(company: {res.companies.get(name) or '?'})")
        for name, reason in res.skipped.items():
            print(f"    skipped:   {name}  ({reason})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
