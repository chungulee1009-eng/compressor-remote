"""CLI: python -m subcon_po <생산계획서> <임가공발주서> [-o 출력폴더]
인자 없이 실행하면 웹화면(http://127.0.0.1:8071)을 띄운다."""
import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import engine, rules
from .report import build_report
from .writer import write_order


def cli(argv):
    ap = argparse.ArgumentParser(prog="python -m subcon_po")
    ap.add_argument("plan")
    ap.add_argument("order")
    ap.add_argument("-o", "--out", default=".")
    ap.add_argument("--year", type=int)
    ap.add_argument("--month", type=int)
    ap.add_argument("--pull", type=int, default=rules.PULL_UP_QTY)
    a = ap.parse_args(argv)
    res = engine.build(a.plan, a.order, year=a.year, month=a.month, pull=a.pull)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{res.year}년 {res.month}월 임가공발주서_수삽갱신_{datetime.now():%y%m%d_%H%M}"
    print(f"{res.year}년 {res.month}월 | 납품 {len(res.deliveries)}행 {sum(d.qty for d in res.deliveries):,}EA | "
          f"제외 {len(res.excluded)} | 미등록 {len(res.unregistered)} | 일정미정 {len(res.no_schedule)}")
    for w in res.warnings:
        print("  ⚠", w)
    print("납품리스트:", build_report(res, out / f"{stem}_납품리스트.xlsx"))
    try:
        p, be = write_order(a.order, out, *res.order_section, engine.output_rows(res), stem)
        print(f"발주서({be}):", p)
    except Exception as e:
        print("발주서 저장 불가:", e)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cli(sys.argv[1:])
    else:
        from .app import main
        main()
