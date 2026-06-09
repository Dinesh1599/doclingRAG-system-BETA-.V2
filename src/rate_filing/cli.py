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
    r = sub.add_parser("run", help="run the pipeline on a PDF")
    r.add_argument("pdf", nargs="?", help="path to a PDF (default: all in INPUT_DIR)")
    r.add_argument("--all", action="store_true", help="process every PDF in INPUT_DIR")
    args = p.parse_args(argv)

    cfg = Config.from_env()
    if args.cmd == "run":
        if args.all or not args.pdf:
            pdfs = sorted(cfg.input_dir.glob("*.pdf"))
        else:
            pdfs = [Path(args.pdf)]
        if not pdfs:
            print("no PDFs to process", file=sys.stderr)
            return 1
        for pdf in pdfs:
            res = run(pdf, cfg)
            print(f"\n== {pdf.name} ==")
            print(f"  workbook: {res.workbook}")
            print(f"  company: {res.company or '(not detected)'}")
            print(f"  bill-pay rows: {res.rows} (from {res.candidate_pages} candidate pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
