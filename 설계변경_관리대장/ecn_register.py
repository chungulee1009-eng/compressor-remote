"""설계변경 통보서 PDF -> 엑셀 누적 관리대장 (설계변경_관리대장.xlsx)

사용:
    python ecn_register.py               # 설변_수신함 폴더의 PDF 일괄 등록 (등록 후 보관 폴더로 이동)
    python ecn_register.py a.pdf b.pdf   # 지정 파일 등록 (보관 폴더로 복사, 원본 유지)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import shutil
import sys
from pathlib import Path

try:
    import pdfplumber
    from openpyxl import Workbook, load_workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.formatting.rule import CellIsRule, FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError as e:  # pragma: no cover
    print(f"[오류] 필요한 패키지가 없습니다: {e}\n설계변경_등록.bat 으로 실행해 주세요.")
    sys.exit(1)

BASE = Path(__file__).resolve().parent
LEDGER_LEGACY = BASE / "설계변경_관리대장.xlsx"      # v1.0 파일명 (발견 시 이어서 사용)
LEDGER_GLOB = "*년_SA_설계변경 관리대장_*.xlsx"
INBOX = BASE / "설변_수신함"
ARCHIVE = BASE / "설변_보관"
BACKUP = BASE / "백업"
BACKUP_KEEP = 20

FONT = "맑은 고딕"
RES = 288                      # 체크박스 판별용 렌더링 해상도(dpi)
RADIO_ON, CHECK_ON = 0.40, 0.12  # 선택 판정 임계값 (중심부 어두운 픽셀 비율)

# ───────────────────────── 공통 유틸 ─────────────────────────

def norm(s) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def clean(s) -> str:
    return re.sub(r"[ \t]+", " ", str(s or "").replace("\xa0", " ")).strip()


def parse_date(s):
    s = clean(s)
    m = re.search(r"(20\d{2}|\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", s)
    if not m:
        return None
    y = int(m.group(1))
    y = y + 2000 if y < 100 else y
    try:
        return dt.date(y, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def month_end(y: int, m: int) -> dt.date:
    if m == 12:
        return dt.date(y, 12, 31)
    return dt.date(y, m + 1, 1) - dt.timedelta(days=1)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ledger_name(year: int, day: dt.date) -> str:
    """대장 파일명 : 2026년_SA_설계변경 관리대장_10.20.xlsx (최종 저장일 기준)"""
    return f"{year}년_SA_설계변경 관리대장_{day:%m.%d}.xlsx"


def find_ledgers():
    """폴더 안의 관리대장 파일 (최근 저장 순)."""
    found = [p for p in BASE.glob(LEDGER_GLOB) if not p.name.startswith("~$")]
    if LEDGER_LEGACY.exists():
        found.append(LEDGER_LEGACY)
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def running_from_temp() -> bool:
    """압축파일을 풀지 않고 탐색기에서 바로 실행하면 매번 새 임시 폴더에서 실행되어 누적이 안 된다."""
    if not sys.platform.startswith("win"):
        return False
    import tempfile
    b = str(BASE).lower()
    return ".zip" in b or b.startswith(str(Path(tempfile.gettempdir()).resolve()).lower())


def alert(title: str, msg: str, error: bool = True) -> None:
    """Windows 메시지 창 (다른 OS 에서는 콘솔 출력만)."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            flags = 0x10 if error else 0x40          # MB_ICONERROR / MB_ICONINFORMATION
            ctypes.windll.user32.MessageBoxW(None, msg, title, flags | 0x40000)  # MB_TOPMOST
            return
        except Exception:
            pass
    print(f"\n[{title}]\n{msg}\n")


class FormError(Exception):
    """설계변경 통보서로 인식할 수 없는 PDF."""


# ───────────────────────── PDF 파싱 ─────────────────────────

KEYMAP = {
    "제목": "제목", "문서번호": "문서번호", "제품/모델명": "제품모델명", "발의자/TEL.": "발의자TEL",
    "작성일자": "작성일자", "BOM변경일자": "BOM변경일자", "라우팅요청일자": "라우팅요청일자",
    "사내": "재고_사내", "업체": "재고_업체", "발주분": "재고_발주분",
    "거래선CONFIRM(S/W)": "거래선CONFIRM", "양산검증(S/W)": "양산검증",
}

# 체크 항목: 행 라벨 -> (필드명, 기본 선택지[라벨 인식 실패 시 순서대로 사용], 다중선택 여부)
OPTION_ROWS = {
    "발생부서": ("발생부서", ["개발1", "개발2", "개발3", "개발4"], False),
    "변경구분": ("변경구분", ["추가", "변경", "삭제", "기타"], False),
    "중요도": ("중요도", ["A", "B", "C"], False),
    "처리": ("재고처리", ["폐기", "RUNNING CHANGE", "기타", "해당없음"], False),
    "폐기방법": ("폐기방법", ["개발청구(품의)", "기타"], False),
    "적용시기": ("적용시기", ["즉시적용", "차기생산분", "재고소진 후 적용"], False),
    "변경사유": ("변경사유", ["원가절감(VE)", "품질향상", "자재수급", "오기개정", "BUYER 요구",
                          "생산성향상", "안전규격", "표준화", "기타"], True),
    "BOM변경": ("BOM변경", ["Y", "N"], False),
    "단계": ("단계", ["개발", "양산"], False),
    "SVCBULLETIN": ("SVC BULLETIN", ["Y", "N"], False),
    "ST변경유무": ("ST변경", ["Y", "N"], False),
}


def group_lines(words, tol=3.0):
    lines = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(lines[-1]["top"] - w["top"]) <= tol:
            lines[-1]["words"].append(w)
        else:
            lines.append({"top": w["top"], "words": [w]})
    for ln in lines:
        ln["words"].sort(key=lambda w: w["x0"])
        ln["x0"] = ln["words"][0]["x0"]
        ln["text"] = " ".join(w["text"] for w in ln["words"])
        ln["n"] = norm(ln["text"])
    return lines


def inside(w, box) -> bool:
    cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def mark_score(img, im, bbox0) -> float:
    """옵션 이미지 왼쪽의 ○/□ 기호 중심부 어두운 비율 (선택되면 높음)."""
    s = RES / 72
    L, T = int((im["x0"] - bbox0[0]) * s), int((im["top"] - bbox0[1]) * s)
    H = max(4, int(im["height"] * s))
    crop = img.crop((L, T, L + int(H * 1.2), T + H))
    w, h = crop.size
    px = crop.load()
    cols = [sum(1 for y in range(h) if px[x, y] < 128) for x in range(w)]
    start = next((x for x, c in enumerate(cols) if c), None)
    if start is None:
        return 0.0
    end = start
    while end + 1 < w and cols[end + 1]:
        end += 1
    rows = [y for y in range(h) if any(px[x, y] < 128 for x in range(start, end + 1))]
    if not rows:
        return 0.0
    cx, cy = (start + end) // 2, (rows[0] + rows[-1]) // 2
    r = max(1, (end - start) // 6)
    vals = [px[x, y] for x in range(cx - r, cx + r + 1) for y in range(cy - r, cy + r + 1)
            if 0 <= x < w and 0 <= y < h]
    return sum(v < 128 for v in vals) / max(1, len(vals))


def detect_options(page, words, top_lo, top_hi):
    img = page.to_image(resolution=RES).original.convert("L")
    left = group_lines([w for w in words if w["x1"] < 82 and top_lo - 5 < w["top"] < top_hi], 4)
    right = group_lines([w for w in words if 280 < w["x0"] < 400 and top_lo - 5 < w["top"] < top_hi], 4)
    seen, cands = set(), {}
    for im in page.images:
        if im["height"] > 25 or im["width"] > 140 or not (top_lo - 5 < im["top"] < top_hi):
            continue
        key = (round(im["x0"]), round(im["top"]))
        if key in seen:
            continue
        seen.add(key)
        cy = im["top"] + im["height"] / 2
        rk = None
        # 오른쪽(x>400) 옵션은 우측 라벨(BOM 변경 등) 우선, 없으면 좌측 라벨 행(변경 사유 등)
        for labels in ([right, left] if im["x0"] > 400 else [left]):
            row = min(labels, key=lambda ln: abs(ln["top"] + 5 - cy), default=None)
            if row is not None and abs(row["top"] + 5 - cy) <= 8:
                rk = next((k for k in OPTION_ROWS if row["n"].endswith(k)), None)
                if rk:
                    break
        if rk is None:
            continue
        box = (im["x0"], im["top"], im["x1"], im["bottom"])
        lbl = clean(" ".join(w["text"] for w in words if inside(w, box)))
        cands.setdefault(rk, []).append((im["x0"], lbl, mark_score(img, im, page.bbox)))
    out = {}
    for rk, items in cands.items():
        field, defaults, multi = OPTION_ROWS[rk]
        items.sort()
        named = [(lbl or (defaults[i] if i < len(defaults) else f"옵션{i + 1}"), sc)
                 for i, (_, lbl, sc) in enumerate(items)]
        if multi:
            out[field] = ", ".join(l for l, sc in named if sc >= CHECK_ON)
        else:
            best = max(named, key=lambda t: t[1])
            out[field] = best[0] if best[1] >= RADIO_ON else ""
    return out


def table_kv(tables):
    kv, approval = {}, ""
    for t in tables:
        rows = t.extract()
        for ri, row in enumerate(rows):
            cells = [c for c in row if c is not None]
            ncells = [norm(c) for c in cells]
            for i, k in enumerate(ncells):
                field = KEYMAP.get(k)
                if k == "기타" and "발주분" in ncells:
                    field = "재고_기타"
                if field and field not in kv:
                    v = cells[i + 1] if i + 1 < len(cells) else ""
                    kv[field] = "" if norm(v) in KEYMAP else clean(v)
            # 결재란 : '발신부서' 오른쪽의 역할/이름/일자
            j = next((ci for ci, c in enumerate(row) if c and norm(c).startswith("발신")), None)
            if j is not None and ri + 2 < len(rows) and not approval:
                parts = []
                for k in range(j + 1, len(row)):
                    role = norm(row[k])
                    name = norm(rows[ri + 1][k]) if k < len(rows[ri + 1]) else ""
                    date = norm(rows[ri + 2][k]) if k < len(rows[ri + 2]) else ""
                    if role and name:
                        parts.append(f"{role}:{name}" + (f"({date})" if date else ""))
                approval = ", ".join(parts)
    return kv, approval


def parse_bom(pdf):
    """BOM CHANGE INFORMATION / APPLY MODEL / 1페이지 BOM 변경정보 표."""
    bom, models, notes, p1rows = [], [], [], []
    for page in pdf.pages:
        for t in page.find_tables():
            rows = t.extract()
            if not rows:
                continue
            hdr = [norm(c) for c in rows[0]]
            if "전/후" in hdr and "자품목코드" in hdr:
                idx = {h: i for i, h in enumerate(hdr)}
                no = parent = ""
                for r in rows[1:]:
                    g = lambda h: clean(r[idx[h]]) if h in idx and idx[h] < len(r) and r[idx[h]] else ""
                    no = g("NO.") or no
                    parent = g("모품목코드") or parent
                    if not g("자품목코드"):
                        continue
                    bom.append({"NO": no, "전후": g("전/후"), "모품목": parent, "자품목": g("자품목코드"),
                                "품목명": g("품목명"), "시작일자": g("시작일자"), "종료일자": g("종료일자"),
                                "수량": g("수량"), "실장위치": g("실장위치")})
            elif "ASSY코드" in hdr and "적용모델" in hdr:
                idx = {h: i for i, h in enumerate(hdr)}
                for r in rows[1:]:
                    g = lambda h: clean(r[idx[h]]) if h in idx and r[idx[h]] else ""
                    if g("ASSY코드") or g("적용모델"):
                        models.append({"ASSY": g("ASSY코드"), "품목명": g("품목명"), "적용모델": g("적용모델")})
            elif "변경전" in hdr and "변경후" in hdr:
                idx = {h: i for i, h in enumerate(hdr)}
                item = ""
                for r in rows[1:]:
                    item = clean(r[idx["항목"]]) if "항목" in idx and r[idx["항목"]] else item
                    notes.append(clean(r[idx["비고"]]) if "비고" in idx and r[idx["비고"]] else "")
                    p1rows.append((item, r[idx["변경전"]] or "", r[idx["변경후"]] or "",
                                   notes[-1]))
    if not bom and p1rows:   # 별첨 BOM 페이지가 없으면 1페이지 표로 대체
        for n, (item, before, after, _) in enumerate(p1rows, 1):
            for tag, cell in (("변경 전", before), ("변경 후", after)):
                ls = [clean(x) for x in str(cell).split("\n") if clean(x)]
                q = next((re.sub(r"^수량\s*:?\s*", "", x) for x in ls if x.startswith("수량")), "")
                loc = next((re.sub(r"^실장위치\s*:?\s*", "", x) for x in ls if x.startswith("실장위치")), "")
                bom.append({"NO": str(n), "전후": tag, "모품목": item, "자품목": ls[0] if ls else "",
                            "품목명": ls[1] if len(ls) > 1 else "", "시작일자": "", "종료일자": "",
                            "수량": q, "실장위치": loc})
    pairs = sorted({b["NO"] for b in bom}, key=lambda x: (len(x), x))
    for b in bom:
        i = pairs.index(b["NO"])
        b["비고"] = notes[i] if i < len(notes) else ""
    return bom, models


def section_lines(lines, start_key, stop_keys):
    out, on = [], False
    for ln in lines:
        if not on:
            if ln["n"].startswith(start_key):
                on = True
            continue
        if any(ln["n"].startswith(k) for k in stop_keys):
            break
        out.append(ln)
    return out


def lines_text(lines, excludes=()):
    rows = []
    for ln in lines:
        ws = [w for w in ln["words"] if not any(inside(w, b) for b in excludes)]
        if ws:
            rows.append((ws[0]["x0"], " ".join(w["text"] for w in ws)))
    if not rows:
        return []
    base = min(x for x, _ in rows)
    return [" " * min(12, int(round((x - base) / 6))) + t for x, t in rows]


def text_rows(text: str, col_width: float) -> int:
    """열 너비 기준 줄바꿈까지 고려한 표시 줄 수 (한글은 영문 약 1.8배 폭)."""
    rows = 0
    for line in str(text).split("\n"):
        w = sum(1.8 if ord(ch) > 0x2E80 else 1.0 for ch in line)
        rows += max(1, -(-int(w) // max(1, int(col_width * 1.05))))
    return rows


def strip_bullet(s: str) -> str:
    return re.sub(r"^\s*(-\.|-|·|•)\s*", "", s)


def parse_pdf(path: Path) -> dict:
    try:
        pdf = pdfplumber.open(str(path))
    except Exception as e:
        raise FormError(f"PDF 열기 실패(손상 파일): {e}")
    with pdf:
        if not pdf.pages:
            raise FormError("페이지가 없는 PDF")
        page = pdf.pages[0]
        dpage = page.dedupe_chars()
        text = dpage.extract_text() or ""
        if len(norm(text)) < 10:
            raise FormError("텍스트가 없는 PDF (스캔 이미지) - 인식 불가")
        if "설계변경통보서" not in norm(text):
            raise FormError("설계변경 통보서 양식이 아님")
        words = dpage.extract_words()
        lines = group_lines(words)
        tables = dpage.find_tables()
        kv, approval = table_kv(tables)
        rec = dict(kv)
        rec["결재"] = approval

        if not rec.get("문서번호"):
            m = re.search(r"SA설변-\d{2}-\d{3,4}(?:-\S+)?", text)
            rec["문서번호"] = m.group(0) if m else ""

        h1 = next((ln["top"] for ln in lines if ln["n"].startswith("1.현상")), page.height / 3)
        rec.update(detect_options(page, words, 140, h1))

        # 본문 영역 : BOM 표, 수발 도장은 제외
        excludes = []
        for t in tables:
            head = norm(" ".join(str(c) for c in (t.extract() or [[]])[0] if c))
            if "변경전" in head and "변경후" in head:
                excludes.append(t.bbox)
        for w in words:
            if re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}", w["text"]) and w["x0"] > 350:
                excludes.append((w["x0"] - 60, w["top"] - 45, w["x1"] + 60, w["bottom"] + 35))
                rec["수발일자"] = w["text"]
        stops = ("■", "SHPS-", "주식회사")
        rec["현상"] = "\n".join(lines_text(section_lines(lines, "1.현상", ("2.원인",) + stops), excludes))
        rec["원인"] = "\n".join(lines_text(section_lines(lines, "2.원인", ("3.대책",) + stops), excludes))
        dl = lines_text(section_lines(lines, "3.대책", stops), excludes)
        rec["대책"] = "\n".join(dl)
        rec["생산기술요청사항"] = next((clean(x.split(":", 1)[1]) for x in dl if "요청사항" in x and ":" in x), "")
        rec["배포처"] = next((clean(ln["text"].split(":", 1)[1]) for ln in lines
                           if "배포처" in ln["n"] and ":" in ln["text"]), "")
        qty = []
        for i, x in enumerate(dl):
            if "재고" in x:
                for y in dl[i:i + 6]:
                    qty += re.findall(r"(\d[\d,]*)\s*EA", y)
        rec["재고수량"] = f"{sum(int(q.replace(',', '')) for q in qty)}EA" if qty else ""

        bom, models = parse_bom(pdf)
        rec["BOM"], rec["적용모델목록"] = bom, models
    return finalize(rec)


# ───────────────────────── 대장 항목 생성 ─────────────────────────

GUBUN_KEYS = {
    "SW": r"MANUAL|매뉴얼|메뉴얼|ROM|F/W|펌웨어|FIRMWARE|BIOS|S/W|소프트웨어|프로그램|PROGRAM|\bOS\b|BSP|안드로이드",
    "기구": r"기구|BRKT|BRACKET|COVER|SCREW|BOX|HINGE|금형|사출|도장|인쇄",
    "HW": r"PCB|PBA|회로|부품|HARNESS|CABLE|커넥터|CONNECTOR|CRYSTAL|POWER|저항|RESISTOR|MLCC|실장|LABEL|라벨|ADAPTOR|어댑터",
}
DEPT_GUBUN = {"개발1": "기구", "개발2": "HW", "개발3": "SW", "개발4": "SW"}
REQUIRED = ["문서번호", "제목", "작성일자", "발생부서", "변경구분", "적용시기", "발의자", "현상", "원인", "대책"]


def finalize(rec: dict) -> dict:
    doc = rec.get("문서번호", "")
    m = re.match(r"(.*?-\d{2}-\d{3,4})", doc)
    rec["설변NO"] = m.group(1) if m else doc
    if not rec.get("발생부서"):
        m = re.search(r"(개발\d)$", doc)
        rec["발생부서"] = m.group(1) if m else ""
    who = rec.get("발의자TEL", "")
    rec["발의자"] = who.split()[0] if who else ""
    m = re.search(r"\d{2,4}-\d{3,4}-\d{4}", who)
    rec["연락처"] = m.group(0) if m else ""
    rec["작성일자_d"] = parse_date(rec.get("작성일자"))

    ny = re.search(r"-(\d{2})-\d{3,4}", doc)
    rec["연도"] = 2000 + int(ny.group(1)) if ny else (rec["작성일자_d"] or dt.date.today()).year

    models = [m["적용모델"] for m in rec.get("적용모델목록", []) if m["적용모델"]]
    rec["적용모델"] = ", ".join(dict.fromkeys(models)) or rec.get("제품모델명", "")

    # 구분(HW/SW/기구) : 제목 키워드 -> 본문 키워드 -> 부서 기본값
    rec["구분"], rec["구분근거"] = "", ""
    for src_name, src in (("제목", rec.get("제목", "")), ("본문", rec.get("현상", "") + rec.get("대책", ""))):
        scores = {g: len(re.findall(p, src.upper())) for g, p in GUBUN_KEYS.items()}
        best = max(scores, key=scores.get)
        if scores[best]:
            rec["구분"], rec["구분근거"] = best, f"{src_name} 키워드"
            break
    if not rec["구분"]:
        rec["구분"] = DEPT_GUBUN.get(rec["발생부서"], "")
        rec["구분근거"] = "부서 기본값" if rec["구분"] else ""

    # 적용일 : 기존 LIST 관례(적용월 말일)
    t, d0 = rec.get("적용시기", ""), rec["작성일자_d"]
    m = re.search(r"(\d{2,4})\s*년\s*(\d{1,2})\s*월", t)
    if m:
        y = int(m.group(1)); y = y + 2000 if y < 100 else y
        rec["적용일"] = month_end(y, int(m.group(2)))
    elif d0 and "즉시" in t:
        rec["적용일"] = month_end(d0.year, d0.month)
    elif d0 and "차기" in t:
        nx = dt.date(d0.year + (d0.month == 12), d0.month % 12 + 1, 1)
        rec["적용일"] = month_end(nx.year, nx.month)
    else:
        rec["적용일"] = None

    missing = [k for k in REQUIRED if not rec.get(k)]
    rec["누락"] = missing
    rec["내용"] = build_content(rec)
    return rec


def build_content(rec: dict) -> str:
    def block(title, body):
        ls = [strip_bullet(x).rstrip() for x in body.split("\n") if x.strip()]
        if not ls:
            return f"{title} : "
        return f"{title} : " + ls[0].strip() + "".join("\n           " + x.strip() for x in ls[1:])

    out = [f"1. {rec.get('제목', '')}", block("2. 현상", rec.get("현상", "")), block("3. 원인", rec.get("원인", ""))]
    dl = [x for x in rec.get("대책", "").split("\n") if x.strip()]
    bom_lines = []
    if rec.get("BOM"):
        parents = list(dict.fromkeys(b["모품목"] for b in rec["BOM"]))
        bom_lines.append("   [BOM 변경] 모품목 : " + ", ".join(p for p in parents if p))
        for no in dict.fromkeys(b["NO"] for b in rec["BOM"]):
            grp = [b for b in rec["BOM"] if b["NO"] == no]
            bom_lines.append(f"     {no}) " + (grp[0].get("비고") or "변경"))
            for b in grp:
                tag = "전)" if "전" in b["전후"] else "후)"
                extra = ", ".join(x for x in (f"수량: {b['수량']}" if b["수량"] else "",
                                              f"실장위치: {b['실장위치']}" if b["실장위치"] else "") if x)
                bom_lines.append(f"          {tag} {b['자품목']}:{b['품목명']} {extra}".rstrip())
    body = []
    inserted = False
    for x in dl:
        body.append(x)
        if bom_lines and not inserted and "BOM" in x.upper() and "변경" in x:
            body += bom_lines
            inserted = True
    if bom_lines and not inserted:
        body += bom_lines
    out.append("4. 대책 : " + ("\n" + "\n".join("   " + x for x in body) if body else ""))
    return "\n".join(out)


def list_values(rec: dict) -> dict:
    """설계변경(YYYY) LIST 18개 열 값 (기존 2025 LIST 기준)."""
    bom_y = rec.get("BOM변경") == "Y"
    routing = rec.get("라우팅요청일자", "")
    has_pba = any(b["모품목"].startswith("JK92") for b in rec.get("BOM", [])) or \
        any("PBA" in m["품목명"] for m in rec.get("적용모델목록", []))
    stock = rec.get("재고처리", "")
    q = {"RUNNING CHANGE": "러닝체인지", "해당없음": "해당없음", "폐기": "폐기", "기타": "기타"}.get(stock, stock)
    if q == "러닝체인지" and rec.get("재고수량"):
        q += f"({rec['재고수량']})"
    remark = [rec.get("적용시기", "")]
    if re.search(r"\d+\s*월", remark[0]):
        remark[0] = re.sub(r"\s*부터$", "", remark[0]) + " 생산분부터 적용"
    if rec.get("생산기술요청사항"):
        remark.append(rec["생산기술요청사항"])
    return {
        "A": rec["설변NO"], "B": rec["작성일자_d"], "C": rec.get("발생부서", ""), "D": rec.get("발의자", ""),
        "E": rec["내용"], "F": rec["적용모델"], "G": rec["구분"], "H": rec["적용일"], "I": rec["적용모델"],
        "J": None,
        "K": (parse_date(rec.get("BOM변경일자")) or rec.get("BOM변경일자")) if bom_y else "-",
        "L": parse_date(routing) or ("-" if "없음" in routing or not bom_y else None),
        "M": None if "사양서" in rec.get("생산기술요청사항", "") else "-",
        "N": None if has_pba else "-",
        "O": None if rec["구분"] == "SW" else "-",
        "P": "-",
        "Q": q, "R": " / ".join(x for x in remark if x),
    }


# ───────────────────────── 엑셀 대장 ─────────────────────────

THIN = Side(style="thin", color="000000")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HDR_FILL = PatternFill("solid", fgColor="D9D9D9")
WARN_FILL = PatternFill("solid", fgColor="FFFF00")
TITLE_FILL = PatternFill("solid", fgColor="0000FF")
F10, F10B = Font(name=FONT, size=10), Font(name=FONT, size=10, bold=True)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

LIST_HEAD = ["설변NO", "접수일", "부서", "작성자", "내용", "적용모델", "구분", "적용일", "적용모델",
             "유효성검증", "BOM", "라우팅", "바이어", "PBA", "작/지", "버전표", "재고처리", "비고"]
LIST_WIDTH = [15, 12.1, 7, 6.8, 82, 22.5, 9.1, 11.3, 20.8, 8.6, 10.4, 11.3, 11.3, 10.3, 9.4, 8.5, 14, 26]
LIST_FIRST = 7

DETAIL_HEAD = ["설변NO", "문서번호", "제목", "제품/모델명", "적용모델", "발생부서", "발의자", "연락처", "작성일자",
               "변경구분", "중요도", "변경사유", "적용시기", "적용일", "단계", "BOM변경", "BOM변경일자",
               "라우팅요청일자", "SVC BULLETIN", "ST변경", "재고_사내", "재고_업체", "재고_발주분", "재고_기타",
               "재고처리", "폐기방법", "재고수량", "거래선CONFIRM", "양산검증", "구분", "구분근거", "현상", "원인",
               "대책", "생산기술요청사항", "배포처", "결재", "BOM변경건수", "파싱상태", "원본파일", "파일해시",
               "등록일시", "비고"]
DETAIL_WIDTH = {"제목": 40, "현상": 45, "원인": 40, "대책": 60, "적용모델": 24, "파싱상태": 30, "원본파일": 40,
                "파일해시": 18, "결재": 30, "배포처": 30, "생산기술요청사항": 30, "비고": 25}
BOM_HEAD = ["설변NO", "모품목", "NO", "전/후", "자품목", "품목명", "수량", "실장위치", "시작일자", "종료일자", "비고"]
DCOL = {h: get_column_letter(i + 1) for i, h in enumerate(DETAIL_HEAD)}


def style_header(ws, row, heads, widths=None):
    for i, h in enumerate(heads, 1):
        c = ws.cell(row, i, h)
        c.font, c.fill, c.alignment, c.border = F10B, HDR_FILL, CENTER, BORDER
        if widths:
            w = widths[i - 1] if isinstance(widths, list) else widths.get(h, 12)
            ws.column_dimensions[get_column_letter(i)].width = w


def list_sheet_name(year: int) -> str:
    return f"설계변경({year})"


def create_list_sheet(wb, year: int, author: str):
    ws = wb.create_sheet(list_sheet_name(year))
    ws.merge_cells("A1:F2")
    c = ws["A1"]
    c.value = f"신규 및 양산 설계변경 관리 현황 LIST ({year})"
    c.font = Font(name=FONT, size=20, bold=True, color="FFFFFF")
    c.fill, c.alignment = TITLE_FILL, Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = ws.row_dimensions[2].height = 20
    for r, (k, v) in enumerate((("작성자 :", author), ("일 자 :", None)), 4):
        ws.cell(r, 1, k).font = F10B
        ws.cell(r, 1).alignment = Alignment(horizontal="right")
        ws.cell(r, 2, v).font = F10B
    ws["B5"].number_format = "yyyy-mm-dd"
    ws["B5"].alignment = Alignment(horizontal="left")
    style_header(ws, 6, LIST_HEAD, LIST_WIDTH)
    ws.freeze_panes = "E7"
    dv1 = DataValidation(type="list", formula1='"HW,SW,기구"', allow_blank=True)
    dv2 = DataValidation(type="list", formula1='"완료,진행중,취소"', allow_blank=True)
    ws.add_data_validation(dv1); ws.add_data_validation(dv2)
    dv1.add("G7:G3000"); dv2.add("J7:J3000")
    ws.conditional_formatting.add("J7:J3000", CellIsRule(operator="equal", formula=['"완료"'],
                                  fill=PatternFill("solid", fgColor="C6EFCE")))
    ws.conditional_formatting.add("J7:J3000", CellIsRule(operator="equal", formula=['"취소"'],
                                  fill=PatternFill("solid", fgColor="BFBFBF")))
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "6:6"
    return ws


class Ledger:
    def __init__(self, path: Path | None):
        if path is not None and path.exists():
            self.wb = load_workbook(path)
        else:
            self.wb = Workbook()
            self.wb.remove(self.wb.active)
        wb = self.wb
        if "상세대장" not in wb.sheetnames:
            ws = wb.create_sheet("상세대장")
            style_header(ws, 1, DETAIL_HEAD, DETAIL_WIDTH)
            ws.freeze_panes = "D2"
        if "BOM변경내역" not in wb.sheetnames:
            ws = wb.create_sheet("BOM변경내역")
            style_header(ws, 1, BOM_HEAD, [15, 14, 5, 8, 14, 45, 7, 22, 11, 11, 18])
            ws.freeze_panes = "A2"
        self.detail = wb["상세대장"]
        self.bom = wb["BOM변경내역"]
        hdr = [c.value for c in self.detail[1]]
        self.records = []
        for row in self.detail.iter_rows(min_row=2, values_only=True):
            if any(v not in (None, "") for v in row):
                self.records.append(dict(zip(hdr, row)))

    def find_dup(self, rec: dict, digest: str):
        for r in self.records:
            if r.get("파일해시") == digest:
                return r, "동일한 PDF 파일"
        for r in self.records:
            if rec["설변NO"] and r.get("설변NO") == rec["설변NO"]:
                return r, "동일 설변NO"
        return None, ""

    def list_sheet(self, year: int):
        name = list_sheet_name(year)
        if name not in self.wb.sheetnames:
            prev = sorted(n for n in self.wb.sheetnames if re.fullmatch(r"설계변경\(\d{4}\)", n))
            author = self.wb[prev[-1]]["B4"].value if prev else ""
            create_list_sheet(self.wb, year, author)
        return self.wb[name]

    def add(self, rec: dict, digest: str, link: str, status: str):
        now = dt.datetime.now().replace(microsecond=0)
        # 1) 설계변경(YYYY) LIST
        ws = self.list_sheet(rec["연도"])
        r = max(LIST_FIRST, ws.max_row + 1)
        while r > LIST_FIRST and ws.cell(r - 1, 1).value in (None, ""):
            r -= 1
        vals = list_values(rec)
        for i, col in enumerate("ABCDEFGHIJKLMNOPQR", 1):
            c = ws.cell(r, i, vals[col])
            c.font, c.border = F10, BORDER
            c.alignment = LEFT if col == "E" else CENTER
            if isinstance(vals[col], dt.date):
                c.number_format = "yyyy-mm-dd" if col in "BH" else "yy.mm.dd"
        ws.cell(r, 1).hyperlink = link
        ws.cell(r, 1).font = Font(name=FONT, size=10, color="0563C1", underline="single")
        if status != "정상":
            ws.cell(r, 1).fill = WARN_FILL
        ws.row_dimensions[r].height = max(40.5, min(409, 13.5 * text_rows(rec["내용"], LIST_WIDTH[4]) + 6))
        ws["B5"] = now.date()
        ws.auto_filter.ref = f"A6:R{r}"

        # 2) 상세대장
        row = {h: rec.get(h, "") for h in DETAIL_HEAD}
        row.update({"제품/모델명": rec.get("제품모델명", ""), "작성일자": rec["작성일자_d"] or rec.get("작성일자", ""),
                    "적용일": rec["적용일"], "BOM변경일자": parse_date(rec.get("BOM변경일자")) or rec.get("BOM변경일자", ""),
                    "BOM변경건수": len(rec.get("BOM", [])), "파싱상태": status, "원본파일": Path(link).name,
                    "파일해시": digest, "등록일시": now, "비고": ""})
        dr = self.detail.max_row + 1
        for i, h in enumerate(DETAIL_HEAD, 1):
            v = row[h]
            c = self.detail.cell(dr, i, v if not isinstance(v, list) else ", ".join(map(str, v)))
            c.font, c.border = F10, BORDER
            c.alignment = LEFT if h in ("제목", "현상", "원인", "대책", "파싱상태") else CENTER
            if isinstance(v, dt.datetime):
                c.number_format = "yyyy-mm-dd hh:mm"
            elif isinstance(v, dt.date):
                c.number_format = "yyyy-mm-dd"
        lc = self.detail.cell(dr, DETAIL_HEAD.index("원본파일") + 1)
        lc.hyperlink = link
        lc.font = Font(name=FONT, size=10, color="0563C1", underline="single")
        if status != "정상":
            self.detail.cell(dr, DETAIL_HEAD.index("파싱상태") + 1).fill = WARN_FILL
        self.detail.auto_filter.ref = f"A1:{get_column_letter(len(DETAIL_HEAD))}{dr}"
        self.records.append(row)

        # 3) BOM변경내역
        for b in rec.get("BOM", []):
            br = self.bom.max_row + 1
            for i, v in enumerate([rec["설변NO"], b["모품목"], b["NO"], b["전후"], b["자품목"], b["품목명"],
                                   b["수량"], b["실장위치"], b["시작일자"], b["종료일자"], b.get("비고", "")], 1):
                c = self.bom.cell(br, i, v)
                c.font, c.border, c.alignment = F10, BORDER, CENTER if i != 6 else LEFT
        self.bom.auto_filter.ref = f"A1:K{max(2, self.bom.max_row)}"

    # 현황요약 / 모델별이력 은 실행할 때마다 다시 만든다
    def rebuild(self):
        wb = self.wb
        years = sorted((int(m.group(1)) for n in wb.sheetnames
                        if (m := re.fullmatch(r"설계변경\((\d{4})\)", n))), reverse=True)
        self._model_history()
        self._summary(years)
        order = ["현황요약"] + [list_sheet_name(y) for y in years] + ["상세대장", "BOM변경내역", "모델별이력"]
        wb._sheets = [wb[n] for n in order if n in wb.sheetnames] + \
            [s for s in wb._sheets if s.title not in order]
        wb.active = 0
        for s in wb.worksheets:
            s.sheet_view.tabSelected = s.title == "현황요약"

    def _model_history(self):
        if "모델별이력" in self.wb.sheetnames:
            del self.wb["모델별이력"]
        ws = self.wb.create_sheet("모델별이력")
        heads = ["적용모델", "최신", "설변NO", "작성일자", "구분", "변경구분", "제목", "적용시기", "발생부서"]
        style_header(ws, 1, heads, [26, 6, 15, 11, 7, 9, 50, 16, 9])
        rows = []
        for r in self.records:
            for m in re.split(r"[,/\n]+", str(r.get("적용모델") or "")):
                if m.strip():
                    rows.append([m.strip(), "", r.get("설변NO"), r.get("작성일자"), r.get("구분"),
                                 r.get("변경구분"), r.get("제목"), r.get("적용시기"), r.get("발생부서")])
        key = lambda x: x[3] if isinstance(x[3], (dt.date, dt.datetime)) else dt.datetime.min
        rows.sort(key=lambda x: (x[0], -key(x).toordinal() if key(x) != dt.datetime.min else 0))
        seen = set()
        for i, row in enumerate(rows, 2):
            if row[0] not in seen:
                row[1] = "★"
                seen.add(row[0])
            for j, v in enumerate(row, 1):
                c = ws.cell(i, j, v)
                c.font, c.border, c.alignment = F10, BORDER, LEFT if j == 7 else CENTER
                if isinstance(v, (dt.date, dt.datetime)):
                    c.number_format = "yyyy-mm-dd"
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:I{max(2, len(rows) + 1)}"

    def _summary(self, years):
        if "현황요약" in self.wb.sheetnames:
            del self.wb["현황요약"]
        ws = self.wb.create_sheet("현황요약", 0)
        D = "상세대장"
        rng = lambda h: f"'{D}'!${DCOL[h]}$2:${DCOL[h]}$5000"
        cy = years[0] if years else dt.date.today().year
        ws.column_dimensions["A"].width = 18
        for col in "BCDEFG":
            ws.column_dimensions[col].width = 14
        ws.merge_cells("A1:G1")
        ws["A1"] = "설계변경 관리 현황 요약"
        ws["A1"].font = Font(name=FONT, size=16, bold=True, color="FFFFFF")
        ws["A1"].fill, ws["A1"].alignment = TITLE_FILL, Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 30
        ws["A2"], ws["B2"] = "기준일", "=TODAY()"
        ws["B2"].number_format = "yyyy-mm-dd"
        for c in (ws["A2"], ws["B2"]):
            c.font = F10B

        def put(r, c, v, bold=False, fill=None, fmt=None, align=CENTER):
            cell = ws.cell(r, c, v)
            cell.font, cell.border, cell.alignment = (F10B if bold else F10), BORDER, align
            if fill:
                cell.fill = fill
            if fmt:
                cell.number_format = fmt
            return cell

        def title(r, text):
            ws.cell(r, 1, text).font = Font(name=FONT, size=11, bold=True, color="0000FF")

        # KPI
        title(4, "■ 핵심 지표")
        done = "+".join(f"COUNTIF('{list_sheet_name(y)}'!$J$7:$J$3000,\"완료\")" for y in years) or "0"
        kpis = [("총 등록건수", f"=COUNTA({rng('설변NO')})"),
                (f"{cy}년 등록", f"=COUNTIFS({rng('작성일자')},\">=\"&DATE({cy},1,1),{rng('작성일자')},\"<\"&DATE({cy + 1},1,1))"),
                ("유효성검증 완료", f"={done}"),
                ("검증 미완료", "=A6-C6"),
                ("확인필요", f"=COUNTIF({rng('파싱상태')},\"확인필요*\")"),
                ("최근 30일", f"=COUNTIFS({rng('작성일자')},\">=\"&(TODAY()-30))"),
                ("검증 완료율", "=IF(A6=0,0,C6/A6)")]
        for i, (k, f) in enumerate(kpis, 1):
            put(5, i, k, bold=True, fill=HDR_FILL)
            put(6, i, f, fmt="0.0%" if "율" in k else "#,##0").font = Font(name=FONT, size=14, bold=True)
        ws.row_dimensions[6].height = 26

        # 부서 x 구분
        r = 8
        title(r, "■ 부서별 · 구분별 건수")
        gub = ["HW", "SW", "기구"]
        for j, h in enumerate(["부서"] + gub + ["합계"], 1):
            put(r + 1, j, h, bold=True, fill=HDR_FILL)
        for i, dept in enumerate(["개발1", "개발2", "개발3", "개발4"], r + 2):
            put(i, 1, dept, bold=True)
            for j, g in enumerate(gub, 2):
                put(i, j, f"=COUNTIFS({rng('발생부서')},$A{i},{rng('구분')},{get_column_letter(j)}${r + 1})")
            put(i, 5, f"=SUM(B{i}:D{i})", bold=True)
        tr = r + 6
        put(tr, 1, "합계", bold=True, fill=HDR_FILL)
        for j in range(2, 6):
            L = get_column_letter(j)
            put(tr, j, f"=SUM({L}{r + 2}:{L}{tr - 1})", bold=True, fill=HDR_FILL)

        # 변경사유 Pareto (현재 건수 순 정렬)
        r = tr + 2
        title(r, "■ 변경사유별 건수 (Pareto)")
        reasons = OPTION_ROWS["변경사유"][1]
        cnt = {x: sum(x in str(rec.get("변경사유") or "") for rec in self.records) for x in reasons}
        reasons = sorted(reasons, key=lambda x: -cnt[x])
        for j, h in enumerate(["변경사유", "건수", "비율", "누적비율"], 1):
            put(r + 1, j, h, bold=True, fill=HDR_FILL)
        first = r + 2
        for i, x in enumerate(reasons, first):
            put(i, 1, x, bold=True)
            put(i, 2, f"=COUNTIF({rng('변경사유')},\"*\"&A{i}&\"*\")")
            put(i, 3, f"=IF(SUM($B${first}:$B${first + len(reasons) - 1})=0,0,B{i}/SUM($B${first}:$B${first + len(reasons) - 1}))", fmt="0.0%")
            put(i, 4, f"=SUM($C${first}:C{i})", fmt="0.0%")

        # 적용시기 / 중요도
        r = first + len(reasons) + 1
        title(r, "■ 적용시기 · 중요도")
        put(r + 1, 1, "적용시기", bold=True, fill=HDR_FILL); put(r + 1, 2, "건수", bold=True, fill=HDR_FILL)
        put(r + 1, 4, "중요도", bold=True, fill=HDR_FILL); put(r + 1, 5, "건수", bold=True, fill=HDR_FILL)
        timing = ["즉시적용", "차기생산분", "재고소진 후 적용"]
        for i, x in enumerate(timing, r + 2):
            put(i, 1, x, bold=True)
            put(i, 2, f"=COUNTIF({rng('적용시기')},A{i})")
        oi = r + 2 + len(timing)
        put(oi, 1, "지정월/기타", bold=True)
        put(oi, 2, f"=COUNTA({rng('설변NO')})-SUM(B{r + 2}:B{oi - 1})")
        for i, x in enumerate(["A", "B", "C"], r + 2):
            put(i, 4, x, bold=True)
            put(i, 5, f"=COUNTIF({rng('중요도')},D{i})")

        # 월별 추이 + 차트
        r = oi + 2
        title(r, f"■ {cy}년 월별 등록 건수")
        put(r + 1, 1, "월", bold=True, fill=HDR_FILL); put(r + 1, 2, "건수", bold=True, fill=HDR_FILL)
        for m in range(1, 13):
            i = r + 1 + m
            put(i, 1, f"{m}월", bold=True)
            nxt = f"DATE({cy + (m == 12)},{m % 12 + 1},1)"
            put(i, 2, f"=COUNTIFS({rng('작성일자')},\">=\"&DATE({cy},{m},1),{rng('작성일자')},\"<\"&{nxt})")
        ch = BarChart()
        ch.title, ch.style, ch.legend = f"{cy}년 월별 설계변경 건수", 10, None
        ch.add_data(Reference(ws, min_col=2, min_row=r + 1, max_row=r + 13), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=r + 2, max_row=r + 13))
        ch.height, ch.width = 7.5, 14
        ch.y_axis.numFmt, ch.y_axis.majorGridlines = "0", None
        ws.add_chart(ch, f"D{r + 1}")

        # 연도별 검증 현황
        r = r + 15
        title(r, "■ 연도별 유효성검증 현황")
        for j, h in enumerate(["연도", "등록", "검증완료", "완료율"], 1):
            put(r + 1, j, h, bold=True, fill=HDR_FILL)
        for i, y in enumerate(years, r + 2):
            s = list_sheet_name(y)
            put(i, 1, f"{y}년", bold=True)
            put(i, 2, f"=COUNTA('{s}'!$A$7:$A$3000)")
            put(i, 3, f"=COUNTIF('{s}'!$J$7:$J$3000,\"완료\")")
            put(i, 4, f"=IF(B{i}=0,0,C{i}/B{i})", fmt="0.0%")
        ws.sheet_view.showGridLines = False
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True

    def years(self):
        return sorted(int(m.group(1)) for n in self.wb.sheetnames
                      if (m := re.fullmatch(r"설계변경\((\d{4})\)", n)))

    def save(self, path: Path):
        self.wb.calculation.fullCalcOnLoad = True   # 열 때 요약 수식 재계산
        self.wb.save(path)


# ───────────────────────── 파일 처리 흐름 ─────────────────────────

def is_locked(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with open(path, "r+b"):
            pass
        return False
    except (PermissionError, OSError):
        return True


def backup(path: Path):
    if not path.exists():
        return
    BACKUP.mkdir(exist_ok=True)
    shutil.copy2(path, BACKUP / f"{dt.datetime.now():%Y%m%d_%H%M%S}_{path.name}")
    olds = sorted(BACKUP.glob("[0-9]*_*.xlsx"))
    for f in olds[:-BACKUP_KEEP]:
        f.unlink()


def unique_target(folder: Path, name: str, digest: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    t = folder / name
    n = 2
    while t.exists() and (digest is None or sha256(t) != digest):
        t = folder / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    return t


def collect(args):
    files = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            files += sorted(p.glob("*.pdf")) + sorted(p.glob("*.PDF"))
        elif p.suffix.lower() == ".pdf" and p.exists():
            files.append(p)
        else:
            print(f"  [건너뜀] PDF 아님: {p.name}")
    return list(dict.fromkeys(files))


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for d in (INBOX, ARCHIVE, BACKUP):
        d.mkdir(exist_ok=True)
    inbox_mode = not argv
    files = collect(argv) if argv else sorted(p for p in INBOX.iterdir()
                                              if p.is_file() and p.suffix.lower() == ".pdf")
    print("=" * 64)
    print(" 설계변경 통보서 PDF -> 설계변경 관리대장(엑셀) 누적 등록")
    print(f" 모드: {'수신함(이동)' if inbox_mode else '드래그(복사)'}   대상 PDF: {len(files)}개")
    print("=" * 64)
    if not files:
        print(f"\n등록할 PDF 가 없습니다. '{INBOX.name}' 폴더에 PDF 를 넣고 다시 실행하세요.")
        return 0
    if running_from_temp():
        alert("설계변경 등록 - 실행 위치 오류",
              "압축파일(zip) 안에서 바로 실행 중입니다.\n이 상태에서는 매번 새 임시 폴더에 저장되어 등록 내용이 누적되지 않습니다.\n\n"
              "zip 파일 우클릭 → [압축 풀기] 로 고정 폴더(예: D:\\SA\\설계변경)에 푼 뒤\n그 폴더의 설계변경_등록.bat 을 실행해 주세요.")
        return 3
    ledgers = find_ledgers()
    src = ledgers[0] if ledgers else None
    if src:
        print(f" 누적 대상 : {src.name}")
        for other in ledgers[1:]:
            print(f"   ※ 다른 대장 파일 무시 : {other.name} (최근 저장 파일 기준으로 누적)")
    else:
        print(" 누적 대상 : (없음) 새 관리대장 생성")
    if src and is_locked(src):
        alert("설계변경 등록 - 저장 불가",
              f"{src.name} 파일이 열려 있습니다.\n엑셀을 닫고 다시 실행해 주세요.\n(PDF 는 이동되지 않았습니다)")
        return 2

    ledger = Ledger(src)
    seen_hash = {}
    plan, results, dups = [], [], []   # plan: (src, dest) 저장 성공 후 실행
    for f in files:
        try:
            digest = sha256(f)
        except OSError as e:
            results.append(("오류", f.name, f"파일 읽기 실패: {e}"))
            continue
        if digest in seen_hash:
            msg = f"이번 실행에서 이미 등록한 파일과 동일 ({seen_hash[digest]})"
            dups.append(f"{f.name}\n   → {msg}")
            results.append(("중복", f.name, msg))
            plan.append((f, INBOX / "중복" if inbox_mode else None))
            continue
        try:
            rec = parse_pdf(f)
        except FormError as e:
            results.append(("오류", f.name, str(e)))
            plan.append((f, INBOX / "오류" if inbox_mode else None))
            continue
        except Exception as e:  # 예상 못한 파싱 오류도 오류 폴더로
            results.append(("오류", f.name, f"처리 실패: {type(e).__name__}: {e}"))
            plan.append((f, INBOX / "오류" if inbox_mode else None))
            continue
        old, why = ledger.find_dup(rec, digest)
        if old:
            reg = old.get("등록일시")
            reg = reg.strftime("%Y-%m-%d %H:%M") if isinstance(reg, dt.datetime) else str(reg or "")
            msg = f"{why} - 이미 등록됨: {old.get('설변NO')} / {old.get('제목')} (등록 {reg})"
            dups.append(f"{f.name}\n   → {msg}")
            results.append(("중복", f.name, msg))
            plan.append((f, INBOX / "중복" if inbox_mode else None))
            continue
        status = "정상" if not rec["누락"] else "확인필요 / 누락: " + ", ".join(rec["누락"])
        dest = unique_target(ARCHIVE / str(rec["연도"]), f.name, digest)
        link = dest.relative_to(BASE).as_posix()
        ledger.add(rec, digest, link, status)
        seen_hash[digest] = rec["설변NO"]
        results.append(("등록" if status == "정상" else "확인필요", f.name, f"{rec['설변NO']} {rec.get('제목', '')}"
                        + ("" if status == "정상" else f"  [{status}]")))
        plan.append((f, dest))

    added = sum(1 for r in results if r[0] in ("등록", "확인필요"))
    target = None
    if added:
        ledger.rebuild()
        target = BASE / ledger_name((ledger.years() or [dt.date.today().year])[-1], dt.date.today())
        same = src is not None and src.resolve() == target.resolve()
        try:
            if not same and is_locked(target):
                raise PermissionError(target.name)
            if src:
                backup(src)
            ledger.save(target)
        except PermissionError:
            alert("설계변경 등록 - 저장 불가",
                  f"{target.name} 저장에 실패했습니다 (파일이 열려 있음).\n엑셀을 닫고 다시 실행해 주세요.\n(PDF 는 이동되지 않았습니다)")
            return 2
        if src and not same:
            src.unlink()          # 이전 날짜 파일명은 정리 (백업 폴더에 보관됨)

    for pdf, dest in plan:   # 저장 성공 후에만 파일 이동/복사
        if dest is None:
            continue
        if dest.suffix.lower() != ".pdf":     # 중복/오류 폴더
            dest = unique_target(dest, pdf.name)
        if dest.exists() and dest.resolve() == pdf.resolve():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if inbox_mode:
            if dest.exists():
                pdf.unlink()
            else:
                shutil.move(str(pdf), str(dest))
        elif not dest.exists():
            shutil.copy2(pdf, dest)

    print()
    for kind, name, msg in results:
        print(f"  [{kind:^5}] {name}\n           {msg}")
    cnt = {k: sum(1 for r in results if r[0] == k) for k in ("등록", "확인필요", "중복", "오류")}
    print("\n" + "-" * 64)
    print(f" 등록 {cnt['등록']}건 | 확인필요 {cnt['확인필요']}건 | 중복 {cnt['중복']}건 | 오류 {cnt['오류']}건")
    if target:
        print(f" 저장: {target}  (누적 총 {len(ledger.records)}건)")
    print("-" * 64)

    if target:
        alert("설계변경 등록 - 완료",
              f"이번 등록 {added}건 / 누적 총 {len(ledger.records)}건\n\n저장 파일 : {target.name}"
              + (f"\n(이전 파일 {src.name} → 새 파일명으로 변경, 이전본은 백업 폴더 보관)" if src and src.name != target.name else ""),
              error=False)
    if dups:
        alert("설계변경 등록 - 중복 PDF",
              f"이미 등록된 설계변경 통보서입니다. 등록하지 않았습니다. ({len(dups)}건)\n\n"
              + "\n\n".join(dups[:10]) + ("\n\n…" if len(dups) > 10 else "")
              + (f"\n\n※ 수신함 PDF 는 '{INBOX.name}\\중복' 폴더로 이동했습니다." if inbox_mode else ""))
    errs = [r for r in results if r[0] == "오류"]
    if errs:
        alert("설계변경 등록 - 인식 오류",
              "\n\n".join(f"{n}\n   → {m}" for _, n, m in errs[:10])
              + (f"\n\n※ 수신함 PDF 는 '{INBOX.name}\\오류' 폴더로 이동했습니다." if inbox_mode else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
