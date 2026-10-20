"""생산계획서 → 수삽 납품 리스트 산출."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import rules
from .rules import WEEKDAY_KR, norm
from .xlread import cell, read_sheets, sheet_names


# ============================================================ 데이터 구조
@dataclass
class PlanRow:
    row: int                 # 엑셀 행번호(1-based)
    category: str
    model: str
    buyer: str
    order_qty: float | None
    days: dict[int, int]     # 생산일(일) → 수량


@dataclass
class Delivery:
    ship: date               # 납품일(D-1 영업일)
    prod: date               # 생산계획일
    model: str               # 수삽 탭에 기입할 모델명(sublist 표기 우선)
    buyer: str
    qty: int
    plan_row: int
    in_sublist: bool
    senjin: str = ""         # B열 선진행
    note: str = ""           # D열 신흥양산

    @property
    def date_text(self) -> str:
        return f"{self.ship.day}({WEEKDAY_KR[self.ship.weekday()]})"


@dataclass
class Result:
    year: int
    month: int
    plan_sheet: str
    deliveries: list[Delivery]
    kept_rows: list[list]                     # 날짜 없는 기존 수동행(보존)
    excluded: list[tuple[PlanRow, str]]
    no_schedule: list[PlanRow]                # 오더는 있는데 일자 계획 없음
    qty_mismatch: list[tuple[PlanRow, float]]  # 일자계획 합 ≠ 오더
    unregistered: list[str]
    unmatched_notes: list[list]               # 매칭 안 된 기존 B/D 메모
    warnings: list[str] = field(default_factory=list)
    order_section: tuple[int, int] = (0, 0)   # 수삽 탭 기입 범위(1-based)


# ============================================================ 영업일
def is_workday(d: date, holidays: set[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def prev_workday(d: date, n: int, holidays: set[date]) -> date:
    while n > 0:
        d -= timedelta(days=1)
        if is_workday(d, holidays):
            n -= 1
    return d


def pull_up(days: list[tuple[int, int]], up: int) -> list[tuple[int, int]]:
    """일수량 +up 당김. 예) 80/72(합152), up=50 → 130/22."""
    remain = sum(q for _, q in days)
    out = []
    for d, q in sorted(days):
        if remain <= 0:
            break
        give = min(remain, q + up)
        out.append((d, give))
        remain -= give
    return out


# ============================================================ 생산계획서 파싱
def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _find_plan_header(grid):
    """('모델구분' 행, {열: 일}, 모델열, 바이어열, 오더열, 요일행)"""
    for r in range(min(len(grid), 30)):
        row = grid[r]
        heads = [norm(v) for v in row]
        if "모델구분" not in heads or "MODEL" not in heads:
            continue
        mcol = heads.index("MODEL")
        bcol = heads.index("BUYER") if "BUYER" in heads else mcol + 1
        ocol = heads.index("오더") if "오더" in heads else None
        daycols = {}
        for c, v in enumerate(row):
            n = _num(v)
            if c > mcol and n is not None and float(n).is_integer() and 1 <= n <= 31:
                daycols[c] = int(n)
        wrow = None
        for rr in range(r + 1, min(r + 5, len(grid))):
            hits = sum(1 for c in daycols if str(cell(grid, rr, c) or "").strip() in WEEKDAY_KR
                       and str(cell(grid, rr, c) or "").strip())
            if hits >= 5:
                wrow = rr
                break
        return r, daycols, mcol, bcol, ocol, heads.index("모델구분"), wrow
    return None


def _year_month(grid, filename: str):
    for r in range(min(len(grid), 5)):
        for v in grid[r]:
            m = re.search(r"(\d{2,4})\s*년\s*(\d{1,2})\s*월", str(v or ""))
            if m:
                y = int(m.group(1))
                return (y + 2000 if y < 100 else y), int(m.group(2))
    m = re.search(r"(20\d{2})\D{0,3}(\d{1,2})\s*월", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def parse_plan(path, year=None, month=None):
    path = Path(path)
    sheets = read_sheets(path)
    order = sheet_names(path)
    best = None
    for name in order:
        grid = sheets.get(name) or []
        h = _find_plan_header(grid)
        if not h:
            continue
        rows = _plan_rows(grid, h)
        if best is None or len(rows) > len(best[2]):
            best = (name, grid, rows, h)
    if best is None:
        raise ValueError("생산계획서에서 '모델구분 / M O D E L / BUYER' 헤더를 찾지 못했습니다.")
    name, grid, rows, h = best
    warnings = []
    ym = (year, month) if year and month else _year_month(grid, path.name)
    if not ym:
        raise ValueError("생산계획서의 년/월을 찾지 못했습니다. 화면에서 년/월을 직접 입력하세요.")
    y, m = ym
    _, daycols, *_rest, wrow = h
    if wrow is not None:   # 요일행으로 년/월 검증
        bad = []
        for c, d in daycols.items():
            w = str(cell(grid, wrow, c) or "").strip()
            try:
                real = WEEKDAY_KR[date(y, m, d).weekday()]
            except ValueError:
                continue
            if w and w != real:
                bad.append(d)
        if bad:
            warnings.append(f"요일 불일치: {y}년 {m}월 기준 {bad[:5]}일 요일이 계획서와 다릅니다. 년/월을 확인하세요.")
    return name, y, m, rows, warnings


def _plan_rows(grid, h) -> list[PlanRow]:
    hr, daycols, mcol, bcol, ocol, ccol, wrow = h
    start = (wrow if wrow is not None else hr) + 1
    out = []
    for r in range(start, len(grid)):
        model = cell(grid, r, mcol)
        cat = str(cell(grid, r, ccol) or "").strip()
        if model in (None, "") or "합계" in cat:
            continue
        model = str(model).strip()
        days = {}
        for c, d in daycols.items():
            q = _num(cell(grid, r, c))
            if q:
                days[d] = days.get(d, 0) + int(round(q))
        out.append(PlanRow(row=r + 1, category=cat, model=model,
                           buyer=str(cell(grid, r, bcol) or "").strip(),
                           order_qty=_num(cell(grid, r, ocol)) if ocol is not None else None,
                           days=days))
    return out


# ============================================================ 임가공 발주서 파싱
SUSAP = "수삽"
SUBLIST = "sublist"


def _order_layout(grid):
    """수삽 탭: (헤더행 idx, 열 dict, 기입 시작 idx, 기입 끝 idx) — 0-based."""
    for r in range(min(len(grid), 10)):
        heads = [norm(v) for v in grid[r]]
        if "DATE" in heads and "MODEL" in heads:
            cols = {k: heads.index(k) for k in ("DATE", "선진행", "신흥양산", "MODEL", "BUYER", "Q'TY")
                    if k in heads}
            if len(cols) < 6:
                raise ValueError(f"수삽 탭 헤더가 부족합니다: {list(cols)}")
            start = r + 1
            if start < len(grid) and cell(grid, start, cols["DATE"]) in (None, "") \
                    and cell(grid, start, cols["MODEL"]) in (None, ""):
                start += 1          # 2단 헤더(MAIN BD/POWER...) 건너뜀
            end = None
            for rr in range(start, len(grid)):
                if norm(cell(grid, rr, cols["DATE"])) == "DATE":
                    end = rr - 1    # 다음 섹션(SMD버퍼) 헤더 직전까지
                    break
            if end is None:
                end = start + 300
            return r, cols, start, end
    raise ValueError("임가공 발주서 '수삽' 탭에서 DATE/MODEL 헤더를 찾지 못했습니다.")


def parse_order(path):
    sheets = read_sheets(path, [SUSAP, SUBLIST])
    if SUSAP not in sheets:
        raise ValueError("임가공 발주서에 '수삽' 탭이 없습니다.")
    if SUBLIST not in sheets:
        raise ValueError("임가공 발주서에 'sublist' 탭이 없습니다.")
    grid = sheets[SUSAP]
    hr, cols, start, end = _order_layout(grid)
    existing = []
    for r in range(start, end + 1):
        vals = {k: cell(grid, r, c) for k, c in cols.items()}
        if any(v not in (None, "") for v in vals.values()):
            existing.append(vals)
    sub = sheets[SUBLIST]
    keys: dict[str, str] = {}
    for r in range(len(sub)):
        v = cell(sub, r, 0)
        if norm(v) == "MODEL":
            continue
        if v not in (None, "") and r >= 6:
            keys.setdefault(norm(v), str(v))
    return cols, (start + 1, end + 1), existing, keys


# ============================================================ 메인 산출
def build(plan_path, order_path, *, year=None, month=None, pull=rules.PULL_UP_QTY,
          lead=rules.LEAD_DAYS, extra_holidays=(), unregistered_note=rules.UNREGISTERED_NOTE) -> Result:
    holidays = set(rules.KR_HOLIDAYS) | set(extra_holidays)
    sheet, y, m, plan_rows, warnings = parse_plan(plan_path, year, month)
    cols, section, existing, sublist = parse_order(order_path)

    excluded, no_sched, mismatch, deliveries = [], [], [], []
    unregistered: dict[str, None] = {}
    for p in plan_rows:
        why = rules.exclude_reason(p.category, p.model)
        if why:
            excluded.append((p, why))
            continue
        if not p.days:
            if p.order_qty:
                no_sched.append(p)
            continue
        total = sum(p.days.values())
        if p.order_qty is not None and abs(total - p.order_qty) > 1e-6:
            mismatch.append((p, total))
        key = norm(p.model)
        in_sub = key in sublist
        model_out = sublist.get(key, p.model)   # sublist 표기(후행 공백 포함) 그대로 → VLOOKUP 일치
        if not in_sub:
            unregistered.setdefault(p.model)
        for d, q in pull_up(list(p.days.items()), pull):
            try:
                prod = date(y, m, d)
            except ValueError:
                warnings.append(f"{p.row}행 {p.model}: {m}월 {d}일은 없는 날짜 — 제외")
                continue
            deliveries.append(Delivery(ship=prev_workday(prod, lead, holidays), prod=prod,
                                       model=model_out, buyer=p.buyer, qty=q, plan_row=p.row,
                                       in_sublist=in_sub))
    deliveries.sort(key=lambda x: (x.ship, x.plan_row))

    # 기존 수삽 행: 날짜+모델+바이어 같으면 선진행/신흥양산 메모 이어받기, 날짜 없는 수동행은 보존
    memo = {}
    kept, unmatched = [], []
    for e in existing:
        if e["DATE"] in (None, ""):
            kept.append([e[k] for k in ("DATE", "선진행", "신흥양산", "MODEL", "BUYER", "Q'TY")])
            continue
        k = (norm(e["DATE"]), norm(e["MODEL"]), norm(e["BUYER"]))
        if e["선진행"] or e["신흥양산"]:
            memo[k] = e
    used = set()
    for dlv in deliveries:
        k = (norm(dlv.date_text), norm(dlv.model), norm(dlv.buyer))
        if k in memo and k not in used:
            used.add(k)
            dlv.senjin = str(memo[k]["선진행"] or "")
            dlv.note = str(memo[k]["신흥양산"] or "")
        if not dlv.in_sublist:
            dlv.note = unregistered_note if not dlv.note else \
                (dlv.note if unregistered_note in dlv.note else f"{unregistered_note} / {dlv.note}")
    for k, e in memo.items():
        if k not in used:
            unmatched.append([e[c] for c in ("DATE", "선진행", "신흥양산", "MODEL", "BUYER", "Q'TY")])

    cap = section[1] - section[0] + 1
    if len(deliveries) + len(kept) > cap:
        warnings.append(f"수삽 기입 범위({cap}행)보다 납품행({len(deliveries) + len(kept)})이 많습니다. "
                        "초과분은 기입되지 않습니다. 발주서에 행을 추가하세요.")
    return Result(year=y, month=m, plan_sheet=sheet, deliveries=deliveries, kept_rows=kept,
                  excluded=excluded, no_schedule=no_sched, qty_mismatch=mismatch,
                  unregistered=list(unregistered), unmatched_notes=unmatched,
                  warnings=warnings, order_section=section)


def output_rows(res: Result) -> list[list]:
    """수삽 탭 A~G 기입값 [DATE, 선진행, 신흥양산, MODEL, BUYER, Q'TY] (C열 미사용)."""
    rows = [[d.date_text, d.senjin or None, d.note or None, d.model, d.buyer, d.qty]
            for d in res.deliveries]
    rows += res.kept_rows
    cap = res.order_section[1] - res.order_section[0] + 1
    return rows[:cap]
