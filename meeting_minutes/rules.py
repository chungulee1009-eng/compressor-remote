"""규칙 기반 한국어 처리: 기한 표현 → 날짜, 담당자(성+직급) 추출, 오프라인 회의록 초안.

AI(Claude) 를 쓸 수 없을 때(API 키 없음 / 오프라인)에도 최소한의 회의록과
Action Item 이 나오도록 하는 대체 경로이며, AI 결과의 기한 보정에도 쓴다.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

# ---------------------------------------------------------------- 기한 파싱

WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}

_NUM_KO = {"하루": 1, "이틀": 2, "사흘": 3, "나흘": 4, "닷새": 5, "일주일": 7, "한 달": 30, "한달": 30}


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _add_month(y: int, m: int, n: int) -> tuple[int, int]:
    m += n
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return y, m


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_due(text: str, base: date) -> date | None:
    """'다음 주 화요일까지', '10/13', '이번 달 말', '내일' 등 → date. 못 찾으면 None."""
    if not text:
        return None
    t = text.replace("담주", "다음 주").replace("다음주", "다음 주").replace("이번주", "이번 주")
    t = t.replace("다다음 주", "다다음주").replace("금주", "이번 주").replace("차주", "다음 주")

    # 1) 명시적 날짜
    m = re.search(r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})", t)
    if m:
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", t) or re.search(r"(?<![\d.])(\d{1,2})[/.](\d{1,2})(?![\d.])", t)
    if m:
        mo, dd = int(m[1]), int(m[2])
        if 1 <= mo <= 12:
            y = base.year
            d = _safe_date(y, mo, dd)
            if d and d < base - timedelta(days=60):  # 12월 회의에서 '1/10' → 내년
                d = _safe_date(y + 1, mo, dd)
            return d
    m = re.search(r"(\d{1,2})\s*월\s*(말|중순|초)", t)
    if m:
        mo = int(m[1])
        y = base.year if mo >= base.month - 2 else base.year + 1
        if 1 <= mo <= 12:
            return {"말": _month_end(y, mo), "중순": date(y, mo, 15), "초": date(y, mo, 5)}[m[2]]
    m = re.search(r"(?<!\d)(\d{1,2})\s*월\s*(까지|중|내|안)", t)
    if m and 1 <= int(m[1]) <= 12:
        mo = int(m[1])
        return _month_end(base.year if mo >= base.month - 2 else base.year + 1, mo)

    # 2) 상대 날짜
    for word, n in (("오늘", 0), ("금일", 0), ("내일", 1), ("명일", 1), ("모레", 2), ("글피", 3)):
        if word in t:
            return base + timedelta(days=n)
    m = re.search(r"(\d+)\s*(일|주|개월|달)\s*(후|뒤|이내|안에|내)", t)
    if m:
        n = int(m[1])
        unit = m[2]
        if unit == "일":
            return base + timedelta(days=n)
        if unit == "주":
            return base + timedelta(weeks=n)
        y, mo = _add_month(base.year, base.month, n)
        return _safe_date(y, mo, min(base.day, calendar.monthrange(y, mo)[1]))
    for word, n in _NUM_KO.items():
        if re.search(word + r"\s*(후|뒤|이내|안에|내)", t):
            return base + timedelta(days=n)

    # 3) 주/요일
    m = re.search(r"(이번 주|다음 주|다다음주)?\s*([월화수목금토일])요일", t)
    if m:
        wd = WEEKDAYS[m[2]]
        mon = _week_monday(base)
        if m[1] == "이번 주":
            return mon + timedelta(days=wd)
        if m[1] == "다음 주":
            return mon + timedelta(days=7 + wd)
        if m[1] == "다다음주":
            return mon + timedelta(days=14 + wd)
        ahead = (wd - base.weekday()) % 7  # 수식어 없음 → 다가오는 해당 요일(오늘 포함)
        return base + timedelta(days=ahead)
    if re.search(r"다다음주", t):
        return _week_monday(base) + timedelta(days=18)
    if re.search(r"다음 주\s*(중|내|까지|안)", t) or t.strip().startswith("다음 주"):
        return _week_monday(base) + timedelta(days=11)  # 다음 주 금요일
    if re.search(r"이번 주\s*(중|내|까지|안)", t) or re.search(r"주말까지", t):
        return _week_monday(base) + timedelta(days=4)  # 이번 주 금요일

    # 4) 월/연 단위
    if re.search(r"연말|올해 안|년말", t):
        return date(base.year, 12, 31)
    if re.search(r"다음 ?달\s*말|익월\s*말", t):
        y, mo = _add_month(base.year, base.month, 1)
        return _month_end(y, mo)
    if re.search(r"(이번 ?달|금월|당월)?\s*(월말|말일|말까지)", t) or "이번 달 말" in t or "이달 말" in t:
        return _month_end(base.year, base.month)
    if re.search(r"다음 ?달\s*(초|까지|중)|익월", t):
        y, mo = _add_month(base.year, base.month, 1)
        return date(y, mo, 10) if "초" in t else _month_end(y, mo)
    if re.search(r"분기\s*말", t):
        q_end_month = ((base.month - 1) // 3 + 1) * 3
        return _month_end(base.year, q_end_month)
    return None


DUE_PHRASE = re.compile(
    r"((20\d{2}[-./년]\s*\d{1,2}[-./월]\s*\d{1,2}일?|\d{1,2}\s*월\s*\d{1,2}\s*일|\d{1,2}\s*월\s*(말|중순|초)|"
    r"(?<![\d.])\d{1,2}[/.]\d{1,2}(?![\d.])|\d{1,2}\s*월(?=\s*(까지|중|내|안))|오늘|금일|내일|명일|모레|글피|\d+\s*(일|주|개월|달)\s*(후|뒤|이내|안에|내)|"
    r"(이번\s?주|다음\s?주|담주|다다음\s?주|금주|차주)?\s*[월화수목금토일]요일|(이번\s?주|다음\s?주|담주|금주|차주)\s*(중|내|안)?|"
    r"(이번\s?달|다음\s?달|이달|금월|당월|익월)\s*(말|초|중)?|월말|말일|연말|분기\s*말)\s*(까지|중으로|안으로|내로|이내)?)"
)


def find_due_phrase(sentence: str) -> str:
    """문장에서 기한 표현 원문을 찾아 반환('다음 주 화요일까지'). 없으면 ''."""
    best = ""
    for m in DUE_PHRASE.finditer(sentence):
        s = m.group(0).strip()
        if len(s) > len(best) and parse_due(s, date(2000, 1, 3)) is not None:
            best = s
    return best


# ---------------------------------------------------------------- 담당자

TITLES = (
    "본부장|공장장|사업부장|센터장|팀장|실장|부장|차장|과장|대리|주임|사원|반장|조장|파트장|그룹장|"
    "이사|상무|전무|부사장|사장|대표|책임|선임|수석|매니저|프로|연구원|기사|계장|직장"
)
SURNAMES = (
    "김이박최정강조윤장임한오서신권황안송류유전홍고문양손배백허남심노하곽성차주우구민진나지엄채원천방공현함"
    "변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경"
)
PERSON = re.compile(rf"(?<![가-힣])([{SURNAMES}][가-힣]{{0,2}})\s?({TITLES})(님)?")
# '이번', '이상' 처럼 성씨로 시작하는 일반 단어 오탐 방지
_NOT_NAMES = {"이번", "이상", "이전", "이후", "정리", "조금", "고객", "공정", "구매", "진행", "전체", "장비", "기존", "차기",
              "문제", "안전", "주간", "우리", "현장", "성능", "한번", "원가", "생산", "품질", "설비", "자재", "영업"}


def find_people(text: str) -> list[str]:
    """'김과장', '박민수 대리', '이차장님' → ['김과장', '박민수대리', '이차장'] (등장 순서, 중복 제거)."""
    out: list[str] = []
    for m in PERSON.finditer(text):
        name, title = m[1], m[2]
        if name in _NOT_NAMES or (len(name) > 1 and name[:2] in _NOT_NAMES):
            continue
        p = f"{name}{title}"
        if p not in out:
            out.append(p)
    return out


# ---------------------------------------------------------------- 오프라인 회의록 초안

ACTION_MARKERS = re.compile(
    r"(해\s?주세요|해\s?주시고|해\s?주시기|부탁|바랍니다|해\s?줘|하세요|하시고|받아\s?주|받아\s?오|챙겨|준비해|작성해|검토해|"
    r"확인해|보고해|정리해|진행해|공유해|하도록|해\s?오|까지\s?(제출|완료|보고|회신))"
)
# '제가 하겠습니다' 류 자기 약속 — 담당자 언급이 같이 있을 때만 Action 으로 본다
COMMIT_MARKERS = re.compile(r"(하겠습니다|할게요|할께요|하겠음|챙기겠습니다)")
DECISION_MARKERS = re.compile(r"(결정|확정|하기로\s?(했|합|하|함)|승인|채택|합의|진행하기로|으로 가겠|로 가죠|로 갑시다)")
ISSUE_MARKERS = re.compile(r"(문제|불량|지연|이슈|리스크|위험|부족|클레임|초과|미달|결품|차질|병목|누락|고장)")
_TS = re.compile(r"^\[\d{1,2}:\d{2}(:\d{2})?\]\s*")


def split_sentences(transcript: str) -> list[str]:
    lines = [_TS.sub("", ln).strip() for ln in transcript.splitlines()]
    text = " ".join(ln for ln in lines if ln)
    parts = re.split(r"(?<=[.?!。])\s+|(?<=(?:요|다|죠|까))\s+(?=[가-힣A-Za-z])", text)
    return [p.strip() for p in parts if p and len(p.strip()) > 1]


def _clean_task(sentence: str, assignee: str, due_phrase: str, fallback: bool = True) -> str:
    s = sentence
    if assignee:
        s = re.sub(rf"{re.escape(assignee[:1])}[가-힣]{{0,2}}\s?{re.escape(assignee[-2:])}(님)?\s*(은|는|이|가|께서|에게|한테)?\s*,?", "", s)
    if due_phrase:
        s = s.replace(due_phrase, "")
    s = re.sub(r"\s*(좀|꼭|반드시)\s+", " ", s)
    s = re.sub(r"[.!]+$", "", s.strip())
    s = re.sub(r"\s?(주시기\s?바랍니다|주세요|주시고|줘요?|부탁\s?(드립니다|합니다|해요|드려요)|바랍니다|하세요)$", "", s)
    s = re.sub(r"(해|하여)$", "", s).strip()
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"[.,!?]+$", "", s).strip(" ,")
    return s or (sentence.strip() if fallback else "")


def offline_minutes(transcript: str, meeting_date: date, title: str = "") -> dict:
    """AI 없이 키워드 규칙만으로 회의록 dict 생성(summarizer 와 같은 스키마)."""
    sents = split_sentences(transcript)
    actions: list[dict] = []
    decisions: list[str] = []
    issues: list[str] = []
    last_person = ""
    for i, s in enumerate(sents):
        people = find_people(s)
        if people:
            last_person = people[0]
        due_phrase = find_due_phrase(s)
        if ACTION_MARKERS.search(s) or (people and COMMIT_MARKERS.search(s)):
            # '다음 주 화요일까지 부탁합니다' 처럼 기한만 있는 짧은 문장 → 직전 Action 에 기한 부여
            if actions and not people and due_phrase and len(_clean_task(s, "", due_phrase, fallback=False)) <= 8:
                if not actions[-1]["due_text"]:
                    actions[-1]["due_text"] = due_phrase
                    d = parse_due(due_phrase, meeting_date)
                    actions[-1]["due_date"] = d.isoformat() if d else ""
                continue
            if not due_phrase and i + 1 < len(sents) and not find_people(sents[i + 1]):
                nxt = find_due_phrase(sents[i + 1])
                if nxt and len(sents[i + 1]) <= 25:
                    due_phrase = nxt
            who = people[0] if people else last_person
            d = parse_due(due_phrase, meeting_date) if due_phrase else None
            actions.append({
                "assignee": who or "미정",
                "task": _clean_task(s, who, due_phrase),
                "due_text": due_phrase,
                "due_date": d.isoformat() if d else "",
                "priority": "보통",
            })
        elif DECISION_MARKERS.search(s):
            decisions.append(s)
        if ISSUE_MARKERS.search(s) and s not in issues:
            issues.append(s)

    summary = sents[:3] if sents else ["(인식된 발언 없음)"]
    return {
        "purpose": title or "회의",
        "summary": summary,
        "discussions": [{"topic": "주요 발언", "content": " ".join(sents[:15])}] if sents else [],
        "decisions": decisions[:15],
        "issues": issues[:15],
        "action_items": actions[:30],
        "pending": [],
        "next_meeting": "",
        "_engine": "offline",
    }


# ---------------------------------------------------------------- 검색 질의 해석

def parse_search_query(q: str, today: date) -> dict:
    """'어제 회의에서 김과장에게 시킨 일' → {'people': ['김과장'], 'date_from':..., 'date_to':..., 'keywords': [...]}"""
    people = find_people(q)
    df = dt = None
    if "그제" in q or "그저께" in q:
        df = dt = today - timedelta(days=2)
    elif "어제" in q:
        df = dt = today - timedelta(days=1)
    elif "오늘" in q:
        df = dt = today
    elif "지난주" in q or "지난 주" in q:
        df = _week_monday(today) - timedelta(days=7)
        dt = df + timedelta(days=6)
    elif "이번 주" in q or "이번주" in q:
        df, dt = _week_monday(today), today
    elif "지난달" in q or "지난 달" in q:
        y, mo = _add_month(today.year, today.month, -1)
        df, dt = date(y, mo, 1), _month_end(y, mo)
    elif "이번 달" in q or "이번달" in q:
        df, dt = date(today.year, today.month, 1), today
    stop = {"어제", "오늘", "그제", "그저께", "지난주", "지난", "주", "이번", "이번주", "지난달", "달", "이번달", "회의", "회의에서",
            "회의때", "시킨", "일", "뭐였지", "뭐야", "뭐", "무엇", "알려줘", "찾아줘", "업무", "에게", "한테", "했던", "있었던", "관련"}
    rest = PERSON.sub(" ", q)
    words = [re.sub(r"(에서|에게|한테|이|가|은|는|을|를|의|로|으로|과|와|\?)$", "", w) for w in re.split(r"\s+", rest)]
    keywords = [w for w in words if len(w) >= 2 and w not in stop]
    return {"people": people, "date_from": df, "date_to": dt, "keywords": keywords}
