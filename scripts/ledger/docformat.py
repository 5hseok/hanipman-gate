"""결정 원장 문서 포맷 — 두 백엔드가 공유한다.

문서 하나가 네 섹션을 갖는다.

    ## 현재 설계   <!-- CURRENT:START/END -->  확정된 결정만. 항상 교체된다
    ## 작업 큐                                 구현 슬라이스 S-n
    ## 결정 로그   <!-- LEDGER:START/END -->   append-only. status 만 뒤집힌다
    ## 진행 로그                               시간순. 항상 마지막 섹션

'## 진행 로그' 가 마지막이라는 것은 불변식이다. append_progress 가 이를 전제로 한다.

표준 라이브러리만 쓴다.
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

CURRENT_START = "<!-- CURRENT:START -->"
CURRENT_END = "<!-- CURRENT:END -->"
LEDGER_START = "<!-- LEDGER:START -->"
LEDGER_END = "<!-- LEDGER:END -->"

_HEADINGS = None


def headings():
    global _HEADINGS
    if _HEADINGS is None:
        with open(os.path.join(HERE, "headings.json"), encoding="utf-8") as f:
            _HEADINGS = json.load(f)
    return _HEADINGS


def heading_for(role, lang="ko"):
    """쓰기용 헤딩 한 줄."""
    r = headings()["roles"][role]
    return r.get(lang) or r["ko"]


def heading_aliases(role):
    """읽기용 — 어떤 언어로 쓰여 있어도 인식한다."""
    return headings()["roles"][role]["aliases"]


def slice_statuses(lang="ko"):
    s = headings()["slice_status"]
    return s.get(lang) or s["ko"]


# ── 섹션 추출 ──────────────────────────────────────────────────────────────

def extract_marked(text, start, end):
    """마커 사이의 알맹이. 마커가 없으면 None."""
    i = text.find(start)
    j = text.find(end)
    if i < 0 or j < 0 or j < i:
        return None
    return text[i + len(start):j].strip("\n")


def replace_marked(text, start, end, inner):
    i = text.find(start)
    j = text.find(end)
    if i < 0 or j < 0 or j < i:
        raise ValueError("마커를 찾지 못했다")
    body = ("\n" + inner.strip("\n") + "\n") if inner.strip() else "\n"
    return text[:i + len(start)] + body + text[j:]


def find_heading(text, role):
    """role 에 해당하는 헤딩의 (시작 오프셋, 실제 헤딩 문자열). 없으면 (-1, None)."""
    for alias in heading_aliases(role):
        m = re.search(r"(?m)^" + re.escape(alias) + r"\s*$", text)
        if m:
            return m.start(), alias
    return -1, None


def extract_heading_section(text, role):
    """헤딩 다음부터 그다음 '## ' 또는 '---' 앞까지."""
    pos, alias = find_heading(text, role)
    if pos < 0:
        return None
    rest = text[pos + len(alias):]
    m = re.search(r"(?m)^(## |---\s*$)", rest)
    return (rest[:m.start()] if m else rest).strip("\n")


# ── 결정 블록 ──────────────────────────────────────────────────────────────

def split_blocks(inner):
    """'### D-n · ...' 헤더 기준 분리. 현재 설계·결정 로그 공용."""
    if not inner or not inner.strip():
        return []
    parts = re.split(r"(?=^### D-\d+ · )", inner, flags=re.MULTILINE)
    return [p for p in parts if p.strip()]


def parse_ledger_block(block):
    """'### D-n · 제목' + '- **key**: value' 메타 + 본문."""
    lines = block.rstrip("\n").split("\n")
    m = re.match(r"^### D-(\d+) · (.+)$", lines[0])
    if not m:
        raise ValueError("결정 로그 블록 헤더 파싱 실패: %r" % lines[0])
    meta = {}
    i = 1
    while i < len(lines) and lines[i].startswith("- **"):
        mm = re.match(r"^- \*\*(.+?)\*\*:\s*(.*)$", lines[i])
        if mm:
            meta[mm.group(1)] = mm.group(2)
        i += 1
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    return {
        "id": int(m.group(1)),
        "title": m.group(2).strip(),
        "status": meta.get("status", "ACTIVE"),
        "topic": meta.get("topic", ""),
        "decided": meta.get("decided", ""),
        "superseded": meta.get("superseded"),
        "mirrors": meta.get("mirrors"),
        "body": "\n".join(lines[i:]).strip("\n"),
    }


def render_ledger_block(e):
    out = ["### D-%d · %s" % (e["id"], e["title"]),
           "- **status**: %s" % e["status"],
           "- **topic**: %s" % e["topic"],
           "- **decided**: %s" % e["decided"]]
    if e.get("superseded"):
        out.append("- **superseded**: %s" % e["superseded"])
    if e.get("mirrors"):
        out.append("- **mirrors**: %s" % e["mirrors"])
    out.append("")
    out.append(e["body"])
    return "\n".join(out)


def parse_current_block(block):
    """'### D-n · topic · 제목' + 본문. 메타 라인 없음."""
    lines = block.rstrip("\n").split("\n")
    m = re.match(r"^### D-(\d+) · ([^·]+) · (.+)$", lines[0])
    if not m:
        raise ValueError("현재 설계 블록 헤더 파싱 실패: %r" % lines[0])
    return {
        "id": int(m.group(1)),
        "topic": m.group(2).strip(),
        "title": m.group(3).strip(),
        "body": "\n".join(lines[1:]).strip("\n"),
    }


def render_current_block(e):
    return "### D-%d · %s · %s\n%s" % (e["id"], e["topic"], e["title"], e["body"])


# ── 진행 로그 ──────────────────────────────────────────────────────────────

def append_progress(text, line, lang="ko"):
    """'## 진행 로그' 끝에 한 줄. 섹션이 없으면 문서 끝에 만든다."""
    pos, alias = find_heading(text, "progress")
    if pos >= 0:
        return text.rstrip() + "\n" + line + "\n"
    return text.rstrip() + "\n\n" + heading_for("progress", lang) + "\n" + line + "\n"


# ── 슬라이스 ───────────────────────────────────────────────────────────────

SLICE_HDR_RE = re.compile(r"(?m)^### S-(\d+)(?: · (.+))?\s*$")
SLICE_STATUS_RE = re.compile(r"(?mi)^(\s*-\s*\*{0,2}status\*{0,2}\s*:\s*)(\S+)")


def set_slice_status(text, n, status):
    """작업 큐의 S-n 블록에서 status 한 줄만 바꾼다. 없으면 헤더 다음에 만든다."""
    hits = list(SLICE_HDR_RE.finditer(text))
    target = next((m for m in hits if int(m.group(1)) == n), None)
    if target is None:
        raise KeyError("S-%d 를 찾지 못했다" % n)
    nxt = next((m.start() for m in hits if m.start() > target.start()), len(text))
    block = text[target.end():nxt]
    if SLICE_STATUS_RE.search(block):
        new = SLICE_STATUS_RE.sub(lambda m: m.group(1) + status, block, count=1)
    else:
        new = "\n- status: %s" % status + block
    return text[:target.end()] + new + text[nxt:]


# ── 문서 골격 ──────────────────────────────────────────────────────────────

def new_document(title, lang="ko", date=""):
    h = lambda r: heading_for(r, lang)
    return (
        "# %s\n\n" % title
        + "%s\n- **생성일**: %s\n- **상태**: 설계\n\n---\n\n" % (h("meta"), date)
        + "%s\n%s\n%s\n\n---\n\n" % (h("current"), CURRENT_START, CURRENT_END)
        + "%s\n\n---\n\n" % h("queue")
        + "%s\n%s\n%s\n\n---\n\n" % (h("ledger"), LEDGER_START, LEDGER_END)
        + "%s\n" % h("progress")
    )


def ensure_markers(text, lang="ko"):
    """마커가 없는 구형 문서에 현재 설계·작업 큐·결정 로그 섹션을 넣는다.
    '## 진행 로그' 는 항상 마지막이어야 하므로 그 앞에 끼운다."""
    if all(m in text for m in (CURRENT_START, CURRENT_END, LEDGER_START, LEDGER_END)):
        return text, False
    h = lambda r: heading_for(r, lang)
    block = (
        "%s\n%s\n%s\n\n---\n\n" % (h("current"), CURRENT_START, CURRENT_END)
        + "%s\n\n---\n\n" % h("queue")
        + "%s\n%s\n%s\n\n---\n\n" % (h("ledger"), LEDGER_START, LEDGER_END)
    )
    pos, _ = find_heading(text, "progress")
    if pos >= 0:
        return text[:pos] + block + text[pos:], True
    return text.rstrip() + "\n\n---\n\n" + block, True
