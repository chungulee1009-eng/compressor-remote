"""업무 규칙(제외 모델, 납기 설정, 휴일). 규칙이 바뀌면 이 파일만 수정."""
from __future__ import annotations

from datetime import date

# ------------------------------------------------------------ 생산계획서 제외 모델
EXCLUDE_CATEGORY = ["해외OPTION", "국내OPTION"]          # 모델구분에 포함
EXCLUDE_MODEL_CONTAINS = ["SA95", "JK75", "JK95", "HCUBE", "ZETA"]  # 모델명에 포함
EXCLUDE_MODEL_PREFIX = ["SKE", "SKC"]                    # 모델명 시작

# ------------------------------------------------------------ 납기 설정
LEAD_DAYS = 1          # 생산일 대비 D-1 (영업일 기준)
PULL_UP_QTY = 50       # 일수량 +50EA 당김
UNREGISTERED_NOTE = "SUBLIST 미등록(신흥양산)"   # sublist 에 없는 모델 → 신흥양산 칸

# ------------------------------------------------------------ 휴일 (토·일 + 아래 날짜 제외)
KR_HOLIDAYS = {
    # 2026
    date(2026, 1, 1), date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 3, 2), date(2026, 5, 1), date(2026, 5, 5), date(2026, 5, 25),
    date(2026, 6, 3), date(2026, 8, 17), date(2026, 9, 24), date(2026, 9, 25),
    date(2026, 10, 5), date(2026, 10, 9), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 2, 8), date(2027, 2, 9), date(2027, 3, 1),
    date(2027, 5, 1), date(2027, 5, 5), date(2027, 5, 13), date(2027, 8, 16),
    date(2027, 9, 14), date(2027, 9, 15), date(2027, 9, 16), date(2027, 10, 4),
    date(2027, 10, 11), date(2027, 12, 27),
}

WEEKDAY_KR = "월화수목금토일"


def norm(s) -> str:
    return "".join(str(s or "").split()).upper()


def exclude_reason(category, model) -> str | None:
    c, m = norm(category), norm(model)
    for k in EXCLUDE_CATEGORY:
        if norm(k) in c:
            return f"모델구분 {k}"
    for k in EXCLUDE_MODEL_CONTAINS:
        if k in m:
            return f"모델명 {k} 포함"
    for k in EXCLUDE_MODEL_PREFIX:
        if m.startswith(k):
            return f"모델명 {k} 시작"
    return None
