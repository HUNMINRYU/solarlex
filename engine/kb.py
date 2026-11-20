"""법령 지식 DB: 벡터 DB와 지식그래프를 SQLite 파일 하나(data/solarlex.db)에 둡니다.

표 구성
  chunks  조문 조각과 Solar 임베딩 벡터(검색용 벡터 DB). build_index.py가 채웁니다.
  nodes   조례(ordinance) · 조(article) · 별표(annex) · 주제(topic) · 상위법(law)
  edges   (src, rel, dst, note, method) — note는 '왜 이어졌는지' 원문 근거, method는 추출 방법(rule=정규식, solar=LLM)
          구조: HAS_ARTICLE · HAS_ANNEX
          조문 사이: REFERS(참조) · EXCEPTION_TO(…에도 불구하고) · PENALIZES(위반 → 과태료) · APPLIES(준용)
          법령 사이: IS_LEGAL_BASIS_OF(상위법 → 조문) · REFERS(다른 조례 인용) · DELEGATES(시장·규칙에 위임)
          의미: DEFINES / USES_TERM(정의 용어) · BENEFITS / OBLIGES(대상자 → 감면·지원 / 금지·의무·과태료) · ABOUT(주제)
  questions  조항마다 Solar가 만든 예상 질문과 그 임베딩(질문 색인). learn.py가 채웁니다.

관계 만드는 법 (멘토가 보여 준 법령 지식그래프 예시를 우리 조례 6종에 맞게 줄였습니다)
  HAS_ARTICLE / HAS_ANNEX  조례 원문 구조 그대로
  CITES                    본문의 '제N조'·'별표 N'(같은 조례), '「서울특별시 ○○ 조례」'(다른 조례)
  IS_LEGAL_BASIS_OF        본문의 '「주차장법」' 같은 상위 법령 → 그 조문 (상위법 본문은 넣지 않고 이름만)
  ABOUT                    조·별표마다 Solar가 고른 주제어(TOPICS 중 1~4개). 조례 사이를 잇는 다리입니다.
                           예: 청년 기본 조례의 건강 지원 조항과 금연 조례의 간접흡연 조항이 '건강'으로 이어짐
  EXCEPTION_TO / PENALIZES / APPLIES  조 참조 뒤의 말로 구분('에도 불구하고', '위반하여'+과태료, '준용')
  DEFINES / USES_TERM      정의 조항의 '"용어"란'을 뽑고, 그 용어가 나오는 다른 조문(다른 조례 포함)을 잇습니다
  BENEFITS / OBLIGES       조문의 항·호마다 대상자 낱말(TARGET_PATTERNS)과 효과 단서(EFFECT_CUES)가 함께 나오면 후보로 뽑고,
                           사람이 전수 검토한 결정(data/targets_review.json: 유지/제외, 효과, 이유)대로 관계를 만듭니다.
                           Solar 검증 의견(solar_check)도 함께 적어 두고, 사람과 다른 60건은 다시 읽어 판정했습니다.
                           예: '장애인'이 주차장 제7조(요금 감면)·도시공원 제20조(입장료 감면)·동물보호 제28조로 이어짐

실행:  python -X utf8 kb.py            그래프 다시 만들기(주제어는 data/topics.json을 재사용)
       python -X utf8 kb.py --topics   주제어를 Solar로 새로 뽑은 뒤 그래프 다시 만들기
       python -X utf8 kb.py --review   검토 결정이 없는 대상자 후보만 보여 주기(조례 원문이 바뀌었을 때)
"""
import json
import re
import sqlite3
import sys

import numpy as np

import config

DB = config.DATA / "solarlex.db"
TOPIC_FILE = config.DATA / "topics.json"
TARGET_REVIEW = config.DATA / "targets_review.json"

# 조례 사이를 잇는 주제어. 사람·상황 중심으로 골라 서로 다른 조례가 같은 말로 만나게 했습니다.
TOPICS = [
    "청년", "아동·청소년", "노인", "장애인", "다자녀·가족", "저소득·취약계층", "국가유공자",
    "건강", "금연·흡연", "음주", "환경·녹지", "교통", "주차", "자전거", "공원·여가", "동물",
    "안전", "요금·사용료", "감면·할인", "과태료·벌칙", "지원·보조금", "시설 설치·관리",
    "위원회", "계획·실태조사", "교육·홍보", "신고·허가·등록", "일자리·창업", "주거", "문화·체육",
    "시민 참여", "점용·사용허가", "사무 위임",
]
# 거의 모든 조례에 있는 행정 절차 주제. 질문이 직접 말하지 않으면 조례 사이를 잇는 데 쓰지 않습니다(연관성이 흐려짐).
GENERIC_TOPICS = {"위원회", "계획·실태조사", "교육·홍보", "사무 위임", "시설 설치·관리", "시민 참여"}

# 조문이 혜택을 주거나 의무를 지우는 대상자와, 원문에서 그 대상을 가리키는 낱말.
# '주차장 이용자'처럼 거의 모든 조문에 붙는 역할은 조례 사이 다리로 쓸모가 없어 넣지 않았습니다(2026-11-19 검토).
TARGET_PATTERNS = {
    "장애인": r"장애인|장애의 정도",
    "국가유공자": r"국가유공자|독립유공자|참전유공자|민주유공자|특수임무유공자|보훈보상대상자|고엽제|의사상자|등록포로|유공자",
    "다자녀 가구": r"다자녀|다둥이|세 자녀|두 자녀 이상|자녀가 2명|자녀가 3명",
    "임산부·영유아 동반": r"임산부|임신부|영유아|영ㆍ유아|유아를 동반|어린 자녀",
    "노인": r"65세 이상|노인|경로우대|고령",
    "청년": r"청년",
    "아동·청소년": r"아동|청소년|어린이|6세 미만|만 \d+세 미만|학생",
    "저소득층": r"기초생활|수급권자|수급자|차상위|저소득|한부모|취약계층",
    "경차·친환경차": r"경형자동차|경차|친환경|환경친화적|전기자동차|저공해|하이브리드|수소전기",
    "흡연자": r"흡연",
    "음주자": r"음주|술을 마시",
    "반려동물 소유자": r"반려동물|반려견|맹견|등록대상동물|동물의 소유자|소유자등",
}
# 동물보호 조례 안에서는 '소유자'·'입양하려는 시민'이 곧 반려동물 소유자입니다.
SCOPED_PATTERNS = {("반려동물 소유자", "동물보호"): r"반려동물|반려견|맹견|등록대상동물|소유자|입양하고자|입양하려는"}
TARGETS = list(TARGET_PATTERNS)
# 효과 단서. 앞에 있는 것이 우선입니다(과태료 > 감면 > 이용 자격 > 지원 > 금지 > 의무). 호(1. 2.)는 소속 항의 머리 문장도 함께 봅니다.
EFFECT_CUES = [("과태료·제재", r"과태료|벌칙|가산금"), ("감면·할인", r"감면|면제|할인|무료|경감"),
               ("이용 자격", r"이용대상|이용 대상|이용할 수 있|발급대상"),
               ("지원·혜택", r"지원|보조|지급|융자|우선|혜택|수당|교육을 실시|제공"),
               ("금지·제한", r"금지|아니 된다|아니된다|할 수 없다|제한"),
               ("의무", r"하여야 한다|해야 한다|의무|부담|명할 수 있|제출하게|준수")]
LAW_NAME = re.compile(r"「[^」]*(?:법|법률|령|규칙|조례)[^」]*」")   # 「장애인복지법」의 '장애인'은 대상자가 아님. 「다둥이 행복카드」는 남김
EFFECTS = {"감면·할인": "BENEFITS", "지원·혜택": "BENEFITS", "이용 자격": "BENEFITS",
           "금지·제한": "OBLIGES", "의무": "OBLIGES", "과태료·제재": "OBLIGES"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks(unit TEXT, id INTEGER, label TEXT, law TEXT, meta TEXT, text TEXT, vec BLOB,
                                  PRIMARY KEY(unit, id));
CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, kind TEXT, law TEXT, name TEXT, title TEXT);
CREATE TABLE IF NOT EXISTS edges(src TEXT, rel TEXT, dst TEXT, note TEXT, method TEXT, PRIMARY KEY(src, rel, dst));
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);
CREATE TABLE IF NOT EXISTS questions(label TEXT, question TEXT, vec BLOB);
"""

# 같은 조례 안의 조 참조. 바로 앞(띄어쓰기 한 칸 포함)이 '법·영·칙·」'이면 국가 법령의 조항이라 건너뜁니다
# (예: '법 제21조', '시행규칙」 제12조', '시행규칙 제26조').
# 뒤에 붙는 항·호 번호까지 읽어 두고, 그 뒤의 말('에도 불구하고', '을 위반하여', '을 준용')로 관계를 가릅니다.
REF = re.compile(r"(?<![법영칙」]\s)(?<![법영칙」])제(\d+)조(의\d+)?(?:\s?제\d+항)?(?:\s?제\d+호)?(?:\s?(?:본문|단서|의 규정))?")
REF_KIND = [("EXCEPTION_TO", re.compile(r"^[^.\n]{0,8}에도\s?불구하고")),
            ("PENALIZES", re.compile(r"^[^.\n]{0,25}위반")),
            ("APPLIES", re.compile(r"^[^.\n]{0,15}준용"))]
DEFINITION = re.compile(r"\d+\.\s*[\"“]([^\"”]{2,15})[\"”]\s?(?:이란|란|이라 함은|라 함은)")
DELEGATION = re.compile(r"(시장이|규칙으로)\s?(따로\s?)?정한다|규칙으로 정하는|시장이 정하는")
USES_TERM_MAX = 15   # 이보다 많은 조문에 나오는 용어('동물' 등)는 연결이 흐려져 빼고, 정의 조항만 남깁니다
ANNEX_REF = re.compile(r"별표\s?(\d*)")
QUOTED = re.compile(r"「([^」]+)」")
AMEND_NOTE = re.compile(r"<(?:개정|신설|전문개정|삭제)[^>]*>|\[(?:본조신설|전문개정|제목개정)[^\]]*\]")


def connect():
    """빌드용(쓰기). 파일이 없으면 만듭니다."""
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    return con


def _read():
    """서비스용(읽기 전용). 서버가 빈 DB 파일을 만들어 '있는 것처럼' 보이지 않게 합니다."""
    if not DB.exists():
        raise FileNotFoundError(f"{DB}가 없습니다. build_index.py·learn.py를 실행하세요.")
    return sqlite3.connect(f"{DB.as_uri()}?mode=ro", uri=True)   # as_uri: 경로의 띄어쓰기를 %20으로


def ready(unit):
    """검색에 쓸 조각이 DB에 있는지. /health와 rag._load가 씁니다."""
    try:
        con = _read()
        n = con.execute("SELECT COUNT(*) FROM chunks WHERE unit=?", (unit,)).fetchone()[0]
        con.close()
        return n > 0
    except (FileNotFoundError, sqlite3.Error):
        return False


# ------------------------------------------------------------ 벡터 DB
def save_chunks(unit, chunks, vecs=None):
    """조각과 벡터를 DB에 씁니다. 같은 단위의 기존 조각은 지우고 새로 넣습니다."""
    con = connect()
    with con:
        con.execute("DELETE FROM chunks WHERE unit=?", (unit,))
        meta_keys = ("label", "law", "text", "id")
        con.executemany(
            "INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
            [(unit, c["id"], c["label"], c["law"],
              json.dumps({k: v for k, v in c.items() if k not in meta_keys}, ensure_ascii=False), c["text"],
              None if vecs is None else np.asarray(vecs[i], dtype=np.float32).tobytes())
             for i, c in enumerate(chunks)])
    con.close()


def load_chunks(unit):
    """(조각 목록, 벡터 행렬 또는 None). 벡터가 하나라도 비어 있으면 None(키워드 검색만)."""
    con = _read()
    rows = con.execute("SELECT id, label, law, meta, text, vec FROM chunks WHERE unit=? ORDER BY id", (unit,)).fetchall()
    con.close()
    if not rows:
        raise FileNotFoundError(f"{DB}에 {unit} 단위 조각이 없습니다. build_index.py를 먼저 실행하세요.")
    chunks = [{**json.loads(meta), "law": law, "label": label, "text": text, "id": i}
              for i, label, law, meta, text, _ in rows]
    vecs = None
    if all(r[5] is not None for r in rows):
        vecs = np.stack([np.frombuffer(r[5], dtype=np.float32) for r in rows])
    return chunks, vecs


def save_questions(rows, vecs):
    """rows: [(인용 라벨, 예상 질문)], vecs: 질문 임베딩. 기존 질문 색인은 지웁니다."""
    con = connect()
    with con:
        con.execute("DELETE FROM questions")
        con.executemany("INSERT INTO questions VALUES(?,?,?)",
                        [(l, q, np.asarray(v, dtype=np.float32).tobytes()) for (l, q), v in zip(rows, vecs)])
    con.close()


def load_questions():
    """(라벨 목록, 질문 목록, 벡터 행렬). 질문 색인이 없으면 빈 목록과 None."""
    try:
        con = _read()
        rows = con.execute("SELECT label, question, vec FROM questions").fetchall()
        con.close()
    except (FileNotFoundError, sqlite3.OperationalError):   # DB·표가 없으면 색인 없이 동작
        return [], [], None
    if not rows:
        return [], [], None
    return [r[0] for r in rows], [r[1] for r in rows], np.stack([np.frombuffer(r[2], dtype=np.float32) for r in rows])


# ------------------------------------------------------------ 지식그래프
def _units(laws):
    """조례마다 그래프 노드가 될 조·별표 단위: (노드 id, 종류, 조례, 이름, 제목, 본문)."""
    for law in laws:
        for a in law["articles"]:
            yield f"{law['name']} {a['article']}", "article", law["name"], a["article"], a["title"], a["text"]
        for ax in law["annexes"]:
            if ax["label"].startswith("별표"):
                yield f"{law['name']} {ax['label']}", "annex", law["name"], ax["label"], ax["title"], ax["text"]


def extract_topics(laws, batch=12):
    """조·별표마다 TOPICS 중 1~4개를 Solar로 고릅니다. 결과는 data/topics.json에 저장해 다시 부르지 않습니다."""
    import upstage
    schema = {"type": "json_schema", "json_schema": {"name": "topics", "strict": True, "schema": {
        "type": "object", "required": ["items"], "additionalProperties": False,
        "properties": {"items": {"type": "array", "items": {
            "type": "object", "required": ["no", "topics"], "additionalProperties": False,
            "properties": {"no": {"type": "integer"},
                           "topics": {"type": "array", "items": {"type": "string", "enum": TOPICS}}}}}}}}}
    units = list(_units(laws))
    out = {}
    for s in range(0, len(units), batch):
        part = units[s:s + batch]
        listing = "\n\n".join(f"[{n}] {u[2]} {u[3]}({u[4]})\n{AMEND_NOTE.sub('', u[5])[:400]}"
                              for n, u in enumerate(part, 1))
        raw = upstage.chat([
            {"role": "system", "content": (
                "너는 조례 조문 분류기다. 조문마다 이 조문이 다루는 대상·상황 주제를 목록에서 1~4개 고른다. "
                "조문에 직접 나오는 대상(청년·노인·장애인 등)과 목적(건강·안전 등)을 우선한다. "
                f"목록: {', '.join(TOPICS)}. 결과는 JSON으로 낸다.")},
            {"role": "user", "content": listing}], response_format=schema)
        for item in json.loads(raw)["items"]:
            if 1 <= item["no"] <= len(part):
                out[part[item["no"] - 1][0]] = [t for t in dict.fromkeys(item["topics"]) if t in TOPICS][:4]
        print(f"주제어 {min(s + batch, len(units))}/{len(units)}")
    TOPIC_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _clause_units(text):
    """조문을 항·호 줄로 나눕니다: [(소속 항의 머리 문장 또는 '', 줄)]."""
    lead, out = "", []
    for line in (l.strip() for l in AMEND_NOTE.sub("", text).split("\n") if l.strip()):
        if re.match(r"^(\d+(의\d+)?|[가-하])\.\s", line):
            out.append((lead, line))
        else:
            lead = line
            out.append(("", line))
    return out


def target_candidates(laws):
    """대상자 낱말과 효과 단서가 같은 항·호(또는 그 머리 문장)에 함께 나오는 (조문, 대상자, 효과 후보, 구절)."""
    out = []
    for law in laws:
        for a in law["articles"]:
            nid = f"{law['name']} {a['article']}"
            for lead, unit in _clause_units(a["text"]):
                body, ctx = LAW_NAME.sub("", unit), LAW_NAME.sub("", f"{lead} {unit}")
                effect = next((e for e, p in EFFECT_CUES if re.search(p, ctx)), None)
                if not effect:
                    continue
                for group, pat in TARGET_PATTERNS.items():
                    pat = next((p for (g, law_word), p in SCOPED_PATTERNS.items() if g == group and law_word in law["name"]), pat)
                    if re.search(pat, body):
                        out.append({"article": nid, "group": group, "unit": unit[:160], "rule_effect": effect})
    return out


def reviewed_targets(laws):
    """사람 검토 결정을 후보에 적용해 {조문: [{group, effect, detail}]}. 결정이 없는 후보는 넣지 않고 수를 돌려줍니다."""
    review = {(r["article"], r["group"], r["unit"]): r
              for r in json.loads(TARGET_REVIEW.read_text(encoding="utf-8"))}
    out, unreviewed = {}, []
    for c in target_candidates(laws):
        r = review.get((c["article"], c["group"], c["unit"]))
        if r is None:
            unreviewed.append(c)
        elif r["keep"] and not any(t["group"] == c["group"] and t["effect"] == r["effect"] for t in out.get(c["article"], [])):
            out.setdefault(c["article"], []).append({"group": c["group"], "effect": r["effect"], "detail": c["unit"]})
    return out, unreviewed


def _ref_kind(after):
    for rel, pat in REF_KIND:
        if pat.match(after):
            return rel
    return "REFERS"


def _snippet(body, start, end, width=36):
    return re.sub(r"\s+", " ", body[max(0, start - 4):end + width]).strip()


def _base_law(name):
    """「주차장법 시행령」 → 주차장법. 국가 법령 이름만 남깁니다(조례·규칙은 None)."""
    name = re.sub(r"\s*(시행령|시행규칙)$", "", name.strip())
    return name if name.endswith("법") else None


def build_graph(laws, topics, targets):
    names = {law["name"] for law in laws}
    nodes, edges = [], {}

    def link(src, rel, dst, note="", method="rule"):
        edges.setdefault((src, rel, dst), (note, method))

    for law in laws:
        nodes.append((law["name"], "ordinance", law["name"], law["name"], ""))
    units = list(_units(laws))
    bodies = {}
    for nid, kind, law, name, title, text in units:
        nodes.append((nid, kind, law, name, title))
        link(law, "HAS_ARTICLE" if kind == "article" else "HAS_ANNEX", nid)
        body = AMEND_NOTE.sub("", text)
        body = body.split(")", 1)[-1] if kind == "article" else body   # '제7조(제목)' 머리 빼기
        bodies[nid] = body
        if kind == "article":
            for m in REF.finditer(body):
                dst = f"{law} 제{m.group(1)}조{m.group(2) or ''}"
                if dst != nid:
                    rel = _ref_kind(body[m.end():])
                    if rel == "PENALIZES" and "과태료" not in body:
                        rel = "REFERS"
                    link(nid, rel, dst, _snippet(body, m.start(), m.end()))
            for m in ANNEX_REF.finditer(body):
                n = m.group(1)
                link(nid, "REFERS", f"{law} 별표 {n}" if n else f"{law} 별표", _snippet(body, m.start(), m.end()))
            if (m := DELEGATION.search(body)):
                nodes.append((f"위임:{law}", "delegation", law, "시장·규칙에 위임", "조례 밖에서 정함"))
                link(nid, "DELEGATES", f"위임:{law}", _snippet(body, m.start(), m.end(), 10))
        for m in QUOTED.finditer(body):
            q = re.sub(r"\s+", " ", m.group(1)).strip()
            if q in names and q != law:
                link(nid, "REFERS", q, _snippet(body, m.start(), m.end()))
            elif (base := _base_law(q)):
                nodes.append((f"법:{base}", "law", "", base, ""))
                link(f"법:{base}", "IS_LEGAL_BASIS_OF", nid, _snippet(body, m.start(), m.end(), 20))
        for t in topics.get(nid, []):
            link(nid, "ABOUT", f"주제:{t}", method="solar")
        for t in targets.get(nid, []):
            link(nid, EFFECTS[t["effect"]], f"대상:{t['group']}", f"{t['effect']}: {t['detail']}", "solar")

    # 정의 용어: 정의 조항→용어(DEFINES), 그 용어를 본문에 쓰는 다른 조문→용어(USES_TERM)
    for nid, kind, law, name, title, text in units:
        if kind != "article":
            continue
        for m in DEFINITION.finditer(bodies[nid]):
            term = m.group(1).strip()
            tid = f"용어:{term}"
            nodes.append((tid, "term", law, term, ""))
            link(nid, "DEFINES", tid, _snippet(bodies[nid], m.start(), m.end(), 40))
            users = [u for u, b in bodies.items() if u != nid and term in b]
            if len(users) <= USES_TERM_MAX:
                for u in users:
                    i = bodies[u].index(term)
                    link(u, "USES_TERM", tid, _snippet(bodies[u], i, i + len(term), 20))

    for t in TOPICS:
        nodes.append((f"주제:{t}", "topic", "", t, ""))
    for t in TARGETS:
        nodes.append((f"대상:{t}", "target", "", t, ""))
    ids = {n[0] for n in nodes}
    rows = sorted((s, r, d, note, method) for (s, r, d), (note, method) in edges.items() if s in ids and d in ids)  # 원문에 없는 조·별표 참조는 버림
    con = connect()
    with con:
        con.execute("DROP TABLE IF EXISTS edges")   # 예전 형식(note 열 없음) DB도 다시 만듭니다
        con.execute("DELETE FROM nodes")
        con.executescript(SCHEMA)
        con.executemany("INSERT OR IGNORE INTO nodes VALUES(?,?,?,?,?)", nodes)
        con.executemany("INSERT INTO edges VALUES(?,?,?,?,?)", rows)
    con.close()
    return len(ids), len(rows)


def load_graph():
    """{'nodes': {id: {...}}, 'out': {src: [(rel, dst)]}, 'into': {dst: [(rel, src)]}, 'note': {(src, rel, dst): 근거}}"""
    con = _read()
    nodes = {r[0]: {"id": r[0], "kind": r[1], "law": r[2], "name": r[3], "title": r[4]}
             for r in con.execute("SELECT id, kind, law, name, title FROM nodes")}
    out, into, note = {}, {}, {}
    for s, rel, d, n in con.execute("SELECT src, rel, dst, note FROM edges"):
        out.setdefault(s, []).append((rel, d))
        into.setdefault(d, []).append((rel, s))
        note[(s, rel, d)] = n or ""
    con.close()
    return {"nodes": nodes, "out": out, "into": into, "note": note}


def main():
    laws = json.loads((config.DATA / "ordinances.json").read_text(encoding="utf-8"))
    if "--topics" in sys.argv or not TOPIC_FILE.exists():
        topics = extract_topics(laws)
    else:
        topics = json.loads(TOPIC_FILE.read_text(encoding="utf-8"))
    targets, unreviewed = reviewed_targets(laws)
    if unreviewed:
        print(f"⚠ 검토 결정이 없는 대상자 후보 {len(unreviewed)}개는 넣지 않았습니다. kb.py --review로 보고 data/targets_review.json에 결정을 더하세요.")
    if "--review" in sys.argv:
        for c in unreviewed:
            print(f"  {c['article']} | {c['group']} | {c['rule_effect']} | {c['unit'][:80]}")
    n, e = build_graph(laws, topics, targets)
    con = connect()
    by_rel = con.execute("SELECT rel, COUNT(*) FROM edges GROUP BY rel ORDER BY rel").fetchall()
    con.close()
    print(f"지식그래프: 노드 {n}개, 관계 {e}개 " + ", ".join(f"{r} {c}" for r, c in by_rel))


if __name__ == "__main__":
    main()
