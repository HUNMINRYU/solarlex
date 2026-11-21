"""4단계: 검색 → Solar 답변 → 검증 → 대회 응답 형식.

처리 흐름
  retrieve()           임베딩 70% + 키워드(BM25) 30% 혼합 검색, 같은 조항 조각은 하나로 합침
  retrieve_expanded()  Solar가 만든 하위 검색어로 2단계 검색(결합형 문항용)
  answer_with_evidence() Solar 답변(문장별 근거 번호) → 자기모순 차단 → Solar Decide 검증 → 대회 JSON

설계 원칙 세 가지
  1. LLM은 근거 '번호'만 고릅니다. 조항 표기(citation)는 코드가 조각 라벨로 만듭니다.
  2. source_url · effective_date도 코드가 채웁니다. LLM이 링크나 날짜를 쓰게 두지 않습니다.
  3. 의심스러우면 '확인 불가'. 근거 없는 답은 맞아도 점수가 없고, 함정 문항에서는 감점입니다.
"""
import functools
import json
import logging
import math
import re
from collections import Counter

import numpy as np

import config
import kb

UNKNOWN = {"answer": "확인 불가", "citation": [], "source_url": "", "effective_date": ""}
MAX_CITATIONS = 3
FIRST_SENTENCE = re.compile(r"(?<=[.다요])\s")

# 네/아니요 첫 단어 바로잡기. "등록수수료를 내야 하나요?"에 "네, 내지 않아도 됩니다"처럼 답하는 일이 프롬프트 규칙만으로는
# 실행마다 다시 생겨, 질문이 부정형이 아닌데 첫 문장이 부정으로 끝나면 "네"를 "아니요"로 바꿉니다.
# ("안 내도 되나요?"처럼 질문이 부정형이면 "네, 내지 않아도 됩니다"가 맞으므로 건드리지 않습니다.)
# 과거 답 797개에 대어 보니 바뀌는 것은 모두 틀린 첫 단어였습니다(2026-11-17).
QUESTION_NEG = re.compile(r"안(?!에|내|전|심|쪽|팎|의|건|정)\s?[가-힣]|않|없|못|말아야|금지")
NEG_END = re.compile(r"(지\s?않아도\s?(됩니다|돼요|된다)|않아도\s?됩니다|지\s?않습니다|않습니다|없습니다|없어요"
                     r"|안\s?됩니다|수\s?없습니다|필요가?\s?없|해당하지\s?않|불가합니다|받지\s?않)[^가-힣]*$")
# 첫 문장이 '…할인되며, 다른 감면과 중복되지 않습니다'처럼 이어지면 끝의 부정은 덛붙인 조건이라,
# 결론이 담긴 첫 절('…할인되며')만 봅니다. 첫 절이 '…내지 않아도 되며'이면 그대로 부정입니다(2026-11-19 점검).
# 덧붙임 뜻인 '며'에서만 나눕니다. '…입양하고 등록하면 내지 않아도 됩니다', '…확인되지만 내지 않아도 됩니다'처럼
# '고·만·나·데'는 조건·양보라 결론이 뒤 절에 오므로 문장 끝을 그대로 봅니다(재검토).
CLAUSE_SPLIT = re.compile(r"(?<=며),?\s")
NEG_HEAD = re.compile(r"(않아도\s?되|지\s?않|없|안\s?되|불가하|받지\s?않)[가-힣]{0,2},?$")


# 의문사 질문(얼마·몇·언제…)에는 네/아니요로 답하지 않습니다(1주차 특강 14장: 네/아니요는 조건 판단형만).
# "몇 년마다 조사하나요?"에 "네, 3년마다 …"처럼 답하는 일이 과거 답 343건(33문항)에서 나왔습니다.
WH = re.compile(r"얼마|몇|몃|며칠|언제(?!든)|어디|무엇|뭔가|뭐|누가|누구|어떻게|어떤|어느|왜"
                r"|\b(how|what|when|where|which|who|why)\b", re.I)   # 영어 질문도 같은 규칙


def is_wh_question(question):
    """물음 문장이 모두 의문사를 포함하면 True. 하나라도 네/아니요형 물음이 있으면 False."""
    clauses = [c for c in re.split(r"[?？]", question) if c.strip()]
    asks = clauses if question.strip().endswith(("?", "？")) else clauses[:-1] or clauses
    return bool(asks) and all(WH.search(c) for c in asks)


def fix_yes_no(question, answer):
    """첫 단어 바로잡기: 의문사 질문의 '네,'는 떼고, 긍정형 질문에 '네, …않아도 됩니다'는 '아니요'로."""
    if not re.match(r"네[,.\s]", answer):
        return answer
    if is_wh_question(question):
        return re.sub(r"^네[,.]?\s*", "", answer, count=1)
    if QUESTION_NEG.search(question):
        return answer
    first = FIRST_SENTENCE.split(answer, 1)[0]
    head = CLAUSE_SPLIT.split(first, 1)[0]
    if NEG_END.search(first) if head == first else NEG_HEAD.search(head):
        return "아니요" + answer[1:]
    return answer
DELEGATES_STRONG = re.compile(r"따로 정[한하]|규칙으로 정|조례로 (따로 )?정|고시[를에] 따|시장이 정한다"
                              r"|(법|영|령|규칙)\s?제\d+조[^.]{0,20}별표\s?\d*[와과] 같다")   # 조례 자신의 '별표 2와 같다'는 위임이 아님
DELEGATES = re.compile(DELEGATES_STRONG.pattern + r"|정하는 바에 따|준용|(법|영|령|규칙|고시|조례)」?\s?(제\d+조[^.]{0,15})?에 따른다")
# '뜻은 다음과 같다'(정의 조항)까지 걸려 함정 X34가 제2조 인용으로 뚫린 적이 있어 '…와 같다'는 뺐습니다.
DISTRICT = re.compile(r"서울특별시\s*\S+구\s")      # 'OO구' 자치구 조례 인용 방지

_cache = {}


# ------------------------------------------------------------ 검색
def _bigrams(text):
    t = re.sub(r"\s+", "", text)
    return [t[i:i + 2] for i in range(len(t) - 1)]


def _load(unit=None):
    unit = unit or config.CHUNK_UNIT
    if unit in _cache:
        return _cache[unit]
    if kb.ready(unit):
        chunks, vecs = kb.load_chunks(unit)   # 벡터 DB(data/solarlex.db)에서 조각과 임베딩을 읽습니다
    else:
        # DB를 아직 만들지 않은 서버(코드만 갈아끼운 경우)에서 모든 답이 '확인 불가'가 되지 않게,
        # 저장소에 든 조각 JSON과 예전 임베딩 파일(있으면)로 검색합니다. /health의 db가 false로 보입니다.
        logging.getLogger("uvicorn.error").warning("%s에 %s 조각이 없어 JSON/npy로 검색합니다", kb.DB, unit)
        chunks = json.loads((config.DATA / f"chunks_{unit}.json").read_text(encoding="utf-8"))
        vec_path = config.DATA / f"vectors_{unit}.npy"
        vecs = np.load(vec_path) if vec_path.exists() else None
    if vecs is not None:
        vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    # 키워드 검색용 (글자 2개 단위 BM25)
    docs = [Counter(_bigrams(c["label"] + " " + c["text"])) for c in chunks]
    df = Counter(g for d in docs for g in d)
    avg = sum(sum(d.values()) for d in docs) / len(docs)
    _cache[unit] = (chunks, vecs, docs, df, avg, _question_index(chunks))
    return _cache[unit]


def _question_index(chunks):
    """학습한 예상 질문 색인(learn.py): (질문 벡터, 조각별 예상 질문 번호 목록). 없으면 None.

    예상 질문은 항 단위 라벨에 붙어 있어, 조 단위 조각은 그 조에 속한 항들의 예상 질문을 모두 씁니다.
    """
    labels, _, qvecs = kb.load_questions()
    if qvecs is None:
        return None
    qvecs = qvecs / np.linalg.norm(qvecs, axis=1, keepdims=True)
    by_label = {}
    for i, l in enumerate(labels):
        by_label.setdefault(l, []).append(i)
        by_label.setdefault(_node_of(l), []).append(i)
    return qvecs, [by_label.get(c["label"], []) for c in chunks]


def _bm25(query, docs, df, avg, k1=1.2, b=0.75):
    n = len(docs)
    q = set(_bigrams(query))
    scores = np.zeros(n)
    for i, d in enumerate(docs):
        ln = sum(d.values())
        s = 0.0
        for g in q:
            if g in d:
                idf = math.log(1 + (n - df[g] + 0.5) / (df[g] + 0.5))
                s += idf * d[g] * (k1 + 1) / (d[g] + k1 * (1 - b + b * ln / avg))
        scores[i] = s
    return scores


PIECES_PER_LABEL = 3   # 호 단위로 나뉠 긴 항은 같은 라벨의 상위 조각 몇 개를 합쳐 근거 하나로 넘깁니다


def retrieve(question, top_k=config.TOP_K, unit=None):
    """임베딩(의미) + 키워드(용어) 혼합 검색. 임베딩이 없으면 키워드만 씁니다. unit="reference"는 상위법 참고 조각."""
    chunks, vecs, docs, df, avg, qindex = _load(unit)
    kw = _bm25(question, docs, df, avg)
    kw_norm = kw / kw.max() if kw.max() > 0 else kw
    if vecs is not None and config.UPSTAGE_API_KEY:
        cos = vecs @ _query_vec(question)
        # 킷 기준선은 0.7:0.3. 평가셋 111문항으로 경사해 보니 0.9:0.1에서 1단계 top-6 포함률이 가장 높았습니다(분석 15절).
        w = config.HYBRID_WEIGHT
        score = w * cos + (1 - w) * kw_norm
        if qindex is not None and config.QINDEX_WEIGHT:
            qcos = qindex[0] @ _query_vec(question)
            score = score + config.QINDEX_WEIGHT * np.array([qcos[ids].max() if ids else 0.0 for ids in qindex[1]])
    else:
        cos, score = None, kw_norm
    if config.USE_LAW_HINT:
        score = score + _law_hint(question, chunks)
    # 상위 60개 후보를 조항(라벨)별로 묶어 top_k개 조항을 고릅니다. 호 단위로 나뉜 긴 항은
    # 같은 라벨의 조각을 최대 PIECES_PER_LABEL개 합쳐, 필요한 호가 빠지지 않게 합니다.
    groups = {}
    for i in np.argsort(score)[::-1][:60]:
        label = chunks[i]["label"]
        if label not in groups and len(groups) == top_k:
            continue
        pieces = groups.setdefault(label, [])
        if len(pieces) < PIECES_PER_LABEL:
            pieces.append(i)
    return [{**chunks[ids[0]], "text": "\n".join(chunks[i]["text"] for i in ids),
             "score": float(score[ids[0]]),
             "cos": float(cos[ids[0]]) if cos is not None else None} for ids in groups.values()]


@functools.lru_cache(maxsize=1024)
def _query_vec(text):
    """질문 임베딩(정규화). 같은 질문을 검색과 지식그래프에서 두 번 쓰므로 기억해 둡니다."""
    import upstage
    q = np.array(upstage.embed([text], config.EMBED_QUERY)[0], dtype=np.float32)
    return q / np.linalg.norm(q)


def _law_stem(law):
    return re.sub(r"\s", "", law.replace("서울특별시", "").replace("조례", ""))


def _law_hint(question, chunks, bonus=0.05):
    """질문이 조례 이름을 직접 말하면(예: '청년 기본 조례') 그 조례 조각에 작은 가산점을 줍니다.

    2주차 강의의 '메타데이터 필터'를 거르기 대신 가산점으로 적용했습니다. 걸러 버리면 여러 조례를
    함께 묻는 질문의 근거를 놓칠 수 있어서입니다. 조례 이름이 없으면 아무것도 바꾸지 않습니다.
    """
    q = re.sub(r"\s", "", question)
    key = ("law_vec", id(chunks))   # 조례 조각과 상위법 참고 조각(unit=reference)은 길이가 달라 따로 둡니다
    if key not in _cache:
        _cache[key] = np.array([c["law"] for c in chunks])
    laws = _cache[key]
    named = [law for law in set(laws) if _law_stem(law) in q]
    if len(named) != 1:
        return 0.0
    return (laws == named[0]) * bonus


EXPAND_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "search_queries",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"queries": {"type": "array", "items": {"type": "string"}, "maxItems": 3}},
            "required": ["queries"],
            "additionalProperties": False,
        },
    },
}


def expand_queries(question, hits=None):
    """결합형 문항용: 질문을 조문 용어로 된 하위 검색어 2~3개로 나눕니다(원칙 조항 + 예외·감면·제재 조항).

    hits를 주면(config.EXPAND_WITH_HITS) 멘토가 말한 흐름대로 1차 검색에서 찾은 조항 목록을 함께 보여 주고,
    그 조항들을 보고 빠진 조항을 찾을 검색어를 다시 만들게 합니다.
    """
    import upstage
    user = question
    if hits:
        found = "\n".join(f"- {h['label']}: {h['text'].split(chr(10), 1)[-1][:80]}" for h in hits)
        user = (f"[질문]\n{question}\n\n[1차 검색에서 찾은 조항]\n{found}\n\n"
                "위 조항만으로 답하기에 모자라거나 빠진 조항(원칙·예외·감면 중복·과태료·절차·요금표)을 찾을 검색어를 만든다.")
    raw = upstage.chat([
        {"role": "system", "content": (
            "너는 서울특별시 조례 검색 도우미다. 질문에 답하려면 찾아야 할 조례 조항을 떠올리고, "
            "그 조항을 찾기 위한 검색어를 조문에 쓰일 법조문 용어로 2~3개 만든다. "
            "원칙 조항과 함께 예외·감면 중복·제재(과태료)·절차 조항이 필요하면 각각 따로 검색어를 만든다. 결과는 JSON으로 낸다.")},
        {"role": "user", "content": user}],
        response_format=EXPAND_SCHEMA)
    return [q for q in json.loads(raw)["queries"] if q.strip()]


PICK_SCHEMA = {"type": "json_schema", "json_schema": {"name": "pick", "strict": True, "schema": {
    "type": "object", "properties": {"target": {"type": "string"}, "reason": {"type": "string"},
                                     "ids": {"type": "array", "items": {"type": "integer"}}},
    "required": ["target", "reason", "ids"], "additionalProperties": False}}}


def pick_delegating(question, cands):
    """위임 조항 후보 중 '질문이 묻는 바로 그 내용'을 맡긴 조항을 고릅니다. (고른 조항 목록, 질문 대상을 조문 용어로 바꾼 말)

    답변 프롬프트 안에서는 근거 12~15개 사이에서 옆 조항을 고르는 일이 잦아, 후보만 따로 보여 주고 묻습니다.
    """
    import upstage
    listing = "\n".join(f"[{i}] {h['label']}\n{h['text'].split(chr(10), 1)[-1][:400]}" for i, h in enumerate(cands, 1))
    raw = upstage.chat([
        {"role": "system", "content": (
            "너는 서울특별시 조례 조항을 가려내는 도우미다. 아래 [후보]는 모두 세부 내용을 시장·규칙·고시·다른 법령에 맡긴 조항이다. "
            "[질문]이 묻는 세부 내용(금액·비율·기준·크기·시간·절차 등)을 맡긴 조항의 번호를 고른다. "
            "먼저 target에 질문의 대상(어떤 시설·행위·사람의 무엇을 묻는지)을 조문에 쓰일 용어로 바꿔 짧게 적는다. "
            "시민이 쓰는 말은 조문 용어로 바꾸고(따릉이→공공자전거, 할인→감면, 주차비→주차요금, 벌금→과태료), 질문자의 사정(나이·자격·사연)은 빼고 묻는 것만 남긴다"
            "(예: '공공자전거 이용요금 감면율'). "
            "reason에는 후보마다 그 조항이 맡긴 대상이 질문의 대상과 같은지 따진다. "
            "과태료·사용료·요금의 부과 기준 전체를 다른 법령의 별표나 규칙에 맡긴 조항은, 질문이 그 조례 분야(공원·주차장·금연구역 등)에서 생긴 과태료·사용료·요금의 금액을 물으면 대상이 같다. "
            "대상이 다른 조항(주제는 비슷하지만 다른 시설·제도·요금에 관한 것)은 고르지 않는다. 보통 하나이고, 꼭 맞는 조항이 없으면 ids를 빈 배열로 둔다. 결과는 JSON으로 낸다.")},
        {"role": "user", "content": f"[질문]\n{question}\n\n[후보]\n{listing}"}],
        response_format=PICK_SCHEMA)
    try:
        got = json.loads(raw)
        ids, target = got["ids"], got.get("target", "")
    except (json.JSONDecodeError, KeyError, TypeError):
        return [], ""
    return [cands[i - 1] for i in dict.fromkeys(ids) if isinstance(i, int) and 1 <= i <= len(cands)][:2], target


ESTABLISHING_SCHEMA = {"type": "json_schema", "json_schema": {"name": "establishing", "strict": True, "schema": {
    "type": "object",
    "properties": {
        "asked": {"type": "string"}, "term": {"type": "string"},
        "candidates": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "integer"}, "establishes": {"type": "string"}, "same_as_asked": {"type": "boolean"}},
            "required": ["id", "establishes", "same_as_asked"], "additionalProperties": False}}},
    "required": ["asked", "term", "candidates"], "additionalProperties": False}}}


def clauses_establishing(question, cands):
    import upstage
    listing = "\n".join(f"[{i}] {h['label']}\n{h['text'].split(chr(10), 1)[-1][:400]}" for i, h in enumerate(cands, 1))
    raw = upstage.chat([
        {"role": "system", "content": (
            "너는 서울특별시 조례 조항을 가려내는 도우미다. [질문]은 어떤 대상의 구체 값(금액·한도·기준·시간·절차 등)을 묻는데, 그 값은 조례에 없다. "
            "asked에 질문이 값을 묻는 대상(그 지원·요금·사용료·수당·배상·제도)을 짧게 적고, term에는 그 대상을 조문에 쓰일 용어로 바꾼 검색어를 적는다"
            "(월세→차임, 보증금→주택임차보증금, 따릉이→공공자전거, 할인→감면, 벌금→과태료. 예: '청년 주거안정 차임 보조'). "
            "[후보]마다 establishes에 그 조항이 두거나 시행하도록 정한 것을 짧게 적고, 그것이 asked와 같은 대상이면 same_as_asked를 true로 한다. "
            "조항이 그 대상을 두기만 하고 금액·한도·기준을 정하지 않았어도 대상이 같으면 true다"
            "(예: '청년 월세 지원금'↔'주택임차보증금 및 차임의 보조 방안 수립·시행', '동물 화장비'↔'공설동물장묘시설 사용료 부과', '분실 배상액'↔'분실한 이용자에게 손해배상 요구'). "
            "주제만 비슷하고 대상이 다르면 false다: 헬멧 과태료↔안전교육, 보상금·위로금↔이미 낸 요금의 반환이나 감면, 지원금↔과태료, "
            "질문이 전제한 사정(시스템 장애, 사고, 분쟁)을 조항이 다루지 않는 경우, 연락처·전화번호·운영 시간표 같은 안내 정보. 결과는 JSON으로 낸다.")},
        {"role": "user", "content": f"[질문]\n{question}\n\n[후보]\n{listing}"}],
        response_format=ESTABLISHING_SCHEMA)
    try:
        got = json.loads(raw)
        same = [c["id"] for c in got["candidates"] if c["same_as_asked"]]
        return [cands[i - 1] for i in dict.fromkeys(same) if 1 <= i <= len(cands)][:2], got["term"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return [], ""


def complete_articles(hits, limit=4):
    """같은 조의 항이 두 개 이상 검색됐다면 그 조가 질문의 핵심이므로, 빠진 나머지 항도 근거에 더합니다.

    예: 가족배려주차장(제25조의2) 제1항·제4항만 잡히고 이용대상을 정한 제2항이 빠지는 경우.
    """
    chunks = _load()[0]
    by_label = {}
    for c in chunks:
        by_label.setdefault(c["label"], []).append(c)
    seen = {h["label"] for h in hits}
    count = {}
    for label in seen:
        m = ARTICLE.match(label)
        if m and label != m.group(1):
            count[m.group(1)] = count.get(m.group(1), 0) + 1
    added = []
    for art in [a for a, n in count.items() if n >= 2]:
        for label in by_label:
            if label.startswith(art + " ") and label not in seen and len(added) < limit:
                pieces = by_label[label][:PIECES_PER_LABEL]
                seen.add(label)
                added.append({**pieces[0], "text": "\n".join(p["text"] for p in pieces), "score": 0.0, "cos": None})
    return hits + added


ANNEX_REF = re.compile(r"\[?별표\s?(\d*)\]?")


def link_annexes(hits, limit=2):
    """별표와 그 별표를 가리키는 조항을 서로 묶어 근거에 더합니다(스타터킷 README '별표가 연결된 조를 함께').

    예: '별표 1(공영주차장 요금표)'이 검색되면 "요금은 별표 1과 같다"는 제6조 제1항을, 반대로 제6조 제1항이
    검색되면 별표 1을 함께 넣습니다. 요금 문항은 별표와 연결 조항 모두 정답으로 인정됩니다(특강 14장).
    """
    chunks = _load()[0]
    by_label = {}
    for c in chunks:
        by_label.setdefault(c["label"], []).append(c)
    seen = {h["label"] for h in hits}
    added = []

    def add(label):
        if label in seen or label not in by_label or len(added) >= limit:
            return
        pieces = by_label[label][:PIECES_PER_LABEL]
        seen.add(label)
        added.append({**pieces[0], "text": "\n".join(p["text"] for p in pieces), "score": 0.0, "cos": None})

    for h in list(hits):
        law, label = h["law"], h["label"]
        m = re.search(r"별표\s?(\d*)$", label)
        if m:   # 별표가 검색됨 → 같은 조례에서 이 별표를 인용하는 조항
            want = f"별표 {m.group(1)}".strip() if m.group(1) else "별표"
            for l, ps in by_label.items():
                if ps[0]["law"] == law and "별표" not in l and any(
                        re.search(re.escape(want).replace("\\ ", r"\s?") + r"(?!\d)", p["text"].split("\n", 1)[-1]) for p in ps):
                    add(l)
        else:   # 조항이 검색됨 → 본문에 나오는 별표
            for n in ANNEX_REF.findall(h["text"].split("\n", 1)[-1]):
                add(f"{law} 별표 {n}" if n else f"{law} 별표")
    return hits + added


def retrieve_expanded(question, top_k=config.TOP_K, per_query=config.EXPAND_PER_QUERY):
    """원 질문 검색 결과에 하위 검색어 결과를 덧붙입니다. 조항 중복은 제거합니다."""
    hits = retrieve(question, top_k)
    if not config.USE_EXPAND:
        return hits
    seen = {h["label"] for h in hits}
    for q in expand_queries(question, hits if config.EXPAND_WITH_HITS else None):
        for h in retrieve(q, per_query):
            if h["label"] not in seen:
                seen.add(h["label"])
                hits.append(h)
    if config.USE_SIBLINGS:
        hits = complete_articles(hits)
    if config.USE_ANNEX_LINK:
        hits = link_annexes(hits)
    if config.USE_DEF_LINK:
        hits = link_definitions(question, hits)
    if config.USE_EXC_LINK:
        hits = link_exceptions(hits)
    return follow_references(hits) if config.USE_REFS else hits


# '"공원"이란 법 제2조제3호의 …'처럼 다른 법을 가리키기만 하는 정의는 답에 보탬 것이 없어 붙이지 않습니다.
POINTER_DEF = re.compile(r"법\s?제\d+조|「[^」]*법[^」]*」")


def link_definitions(question, hits, limit=2):
    """질문에 나온 용어의 정의 조항(검색된 조례의 '"흡연"이란 …')을 근거에 더합니다.

    예: '불 붙인 담배를 들고만 있어도 흡연인가'는 금연조례 제2조의 '흡연' 정의가 있어야 답할 수 있는데 검색에는 잘 안 걸립니다(dev D59).
    정의 조항 전체 대신 머리 문장과 그 용어의 호만 남깁니다. 용어 찾기는 지식그래프 DEFINES와 같은 kb.DEFINITION 규칙입니다.
    """
    q = re.sub(r"\s", "", question)
    laws = {h["law"] for h in hits}
    seen = {h["label"] for h in hits}
    added = []
    for c in _load()[0]:
        if len(added) >= limit or c["law"] not in laws or c["label"] in seen:
            continue
        lines = c["text"].split("\n")
        keep = [l for l in lines if (m := kb.DEFINITION.search(l)) and re.sub(r"\s", "", m.group(1)) in q
                and not POINTER_DEF.search(l)]
        if keep:
            seen.add(c["label"])
            added.append({**c, "text": "\n".join(lines[:1] + keep), "score": 0.0, "cos": None})
    return hits + added


EXCEPTION_OF = re.compile(r"제(\d+)조(의\d+)?\s?제(\d+)항(?:\s?본문)?에도 불구하고")


def link_exceptions(hits, limit=2):
    """예외 조항('제23조제2항에도 불구하고 … 감면할 수 있다')이 검색되면 그가 바꾸는 원칙 조항을 근거에 더합니다.

    예: 청사 부설주차장 감면(제23조의2)만 찾고 '요금을 받을 수 있다'는 원칙(제23조 제2항)을 놓쳐 인용이 빠지던 holdout H22.
    """
    by_label = {c["label"]: c for c in _load()[0]}
    seen = {h["label"] for h in hits}
    added = []
    for h in hits:
        for m in EXCEPTION_OF.finditer(h["text"]):
            label = f"{h['law']} 제{m.group(1)}조{m.group(2) or ''} 제{m.group(3)}항"
            if len(added) < limit and label in by_label and label not in seen:
                seen.add(label)
                added.append({**by_label[label], "score": 0.0, "cos": None})
    return hits + added


# 같은 조례 안의 조항 참조: '제6조제1항', '제23조의2', '제7조 제3항'. 앞에 '법·영·규칙·」'가 붙으면
# 국가 법령(다른 법)의 조항이라 건너뜁니다.
REF = re.compile(r"(?<![법영칙」]\s)(?<![법영칙」])제(\d+)조(의\d+)?(?:\s?제(\d+)항)?")


def follow_references(hits, limit=3):
    """검색된 조항이 같은 조례의 다른 조항을 가리키면(예: '제6조제1항 본문에도 불구하고') 그 조항을 근거에 덧붙입니다.

    결합형 문항에서 원칙·예외·제재 조항이 서로를 인용하는 경우가 많아, 참조를 따라가 빠진 조항을 채웁니다
    (RefWalk, EMNLP 2026 Findings의 '참조 따라 확장' 아이디어). 최대 limit개까지만 추가합니다.
    """
    chunks = _load()[0]
    by_label = {}
    for c in chunks:
        by_label.setdefault(c["label"], []).append(c)
    seen = {h["label"] for h in hits}
    added = []
    for h in list(hits):
        body = h["text"].split("\n", 1)[-1]          # 첫 줄 [조례명 제N조(제목)] 머리글은 제외
        for art, sub, para in REF.findall(body):
            label = f"{h['law']} 제{art}조{sub}" + (f" 제{para}항" if para else "")
            if label in seen or label not in by_label:
                continue
            pieces = by_label[label][:PIECES_PER_LABEL]
            seen.add(label)
            added.append({**pieces[0], "text": "\n".join(p["text"] for p in pieces),
                          "score": 0.0, "cos": None})
            if len(added) >= limit:
                return hits + added
    return hits + added


ARTICLE = re.compile(r"^(.*? 제\d+조(?:의\d+)?)")


def _overlap(statement, text):
    """주장 문장의 글자 2-gram 중 조항 본문에도 있는 비율(머리글 줄은 빼고 줌)."""
    s = set(_bigrams(statement))
    body = set(_bigrams(text.split("\n", 1)[-1]))
    return len(s & body) / len(s) if s else 0.0


def refine_paragraphs(claims, hits, margin=0.15):
    """Solar가 고른 근거가 맞는 조의 다른 항이면, 같은 조의 항들 중 주장 문장과 글자가 가장 잘 겹치는 항으로 바꿉니다.

    남은 오답 대부분이 '조는 맞고 항이 틀린' 경우라(예: 금주구역 과태료를 제23조 제4항 대신 제1~3항으로 인용),
    API 호출 없이 글자 겹침으로 바로잡습니다. 더 나은 항이 margin 이상 앞설 때만 바꿉니다.
    """
    chunks = _load()[0]
    by_label = {}
    for c in chunks:
        by_label.setdefault(c["label"], []).append(c)
    used = []
    for c in claims:
        i = c.get("evidence_id", 0)
        if not 1 <= i <= len(hits):
            continue
        h = hits[i - 1]
        m = ARTICLE.match(h["label"])
        siblings = [l for l in by_label if m and (l == m.group(1) or l.startswith(m.group(1) + " "))]
        best, best_score = h, _overlap(c["statement"], h["text"])
        for label in siblings:
            if label == h["label"]:
                continue
            text = "\n".join(p["text"] for p in by_label[label][:PIECES_PER_LABEL])
            sc = _overlap(c["statement"], text)
            if sc >= best_score + margin:
                best = {**by_label[label][0], "text": text, "score": 0.0, "cos": None}
                best_score = sc
        # 같은 조례의 다른 조항 중 이미 근거에 있는 것도 비교합니다. 주장이 다른 조를 거의 그대로 옮긴 것인데
        # 엉뚱한 조(예: 정의 조항)를 고른 경우를 바로잡습니다.
        for other in hits:
            if other["law"] != h["law"] or other["label"] == best["label"]:
                continue
            sc = _overlap(c["statement"], other["text"])
            if sc >= best_score + margin:
                best, best_score = other, sc
        used.append(best)
    return used


# ------------------------------------------------------------ 답변
SYSTEM = """너는 서울특별시 조례 질의응답 도우미다.
규칙:
- 반드시 아래 [근거]에 적힌 내용만으로 답한다. 근거에 없는 내용은 추측하지 않는다.
- 답은 일반 시민이 이해할 수 있는 쉬운 말로 2~4문장으로 쓴다. 금액·비율·나이 같은 숫자는 그대로 쓴다.
- 예/아니요로 답할 수 있는 질문은 답을 "네" 또는 "아니요"로 시작한다. 첫 단어는 질문의 말을 그대로 받아 답한다.
  예) "돈을 내야 하나요?"에 안 내도 되면 "아니요, 내지 않아도 됩니다"("네, 내지 않아도 됩니다"는 틀림). "할인되나요?"에 할인되면 "네".
  질문자가 밝힌 상황(나이·자격 등)이 근거의 조건에 해당하면 그 상황의 결론으로 답한다(예: "11주 다녔는데 신청되나요?"에 10주 이상 이용 조건이 있으면 "네").
- claims에는 답의 핵심 사실 하나마다 {statement, evidence_id}를 넣는다. evidence_id는 그 사실이 문자 그대로 적혀 있는 근거 번호다.
- 근거의 수치는 질문이 묻는 바로 그 대상(그 시설·요금·제도)에 관한 것일 때만 답에 쓴다. 이름이 비슷해도 다른 대상의 요금표·기준(예: 놀이터 이용료를 묻는데 근거에는 주차장 요금표뿐)이면 그 수치를 쓰지 않는다.
- 같은 조의 여러 항이 함께 있으면, 항마다 내용을 비교해 그 사실을 실제로 규정한 항을 고른다. 주제만 비슷한 항은 고르지 않는다.
- 원칙과 예외(감면 중복 금지, 과태료, 절차 등)를 함께 썼다면 각각의 근거를 따로 claims에 넣는다.
- 근거에 "다만"으로 시작하는 단서나 예외가 있고, 그것이 질문자의 결론에 조건을 붙이거나 결론을 바꾸면 답에 한 문장으로 함께 쓴다.
- 질문이 여러 가지를 묻으면(예: 몇 년마다 세우는지, 매년 무엇을 세우는지, 어디에 보고하는지) 물음마다 그것을 규정한 조항을 따로 claims에 넣는다. 서로 다른 항의 내용을 claim 하나에 합치지 않는다.
- 질문이 계산(금액·인원·기간 등)을 요구하면 근거의 수치로 계산한 최종 값까지 답에 쓴다(예: 가액 2억원, 연 5/1,000, 3개월이면 25만원).
- given에는 질문자가 말한 수치(시간·나이·인원·금액·면적·기간 등)마다 "질문자의 수치 ↔ 근거의 기준값 → 충족 여부"를 한 줄씩 적는다
  (예: "마감 90분 전 입차 ↔ 운영 종료 2시간 이내 → 해당"). 질문에 수치가 없으면 빈 배열이다.
  kind가 answer이면 given에 적은 질문자의 수치와 기준값을 모두 answer에 숫자 그대로 쓴다(예: '90분 전에 들어오셨으므로 운영 종료 2시간 이내에 해당해').
- check를 answer보다 먼저 쓴다. 근거의 조건(수치·비율·기간·합산 방법·예외)을 질문자가 말한 사실에 하나씩 대입해 충족인지 미충족인지 따지고 결론을 낸다.
  '이상·이하·초과·미만'은 경계값을 정확히 따진다(50% 이상이면 정확히 50%도 충족). 여러 값을 합치거나 하나로 보는 규정이 있으면 합산한 값으로 따진다.
  질문자가 말한 수치(인원·나이·면적·기간·시간 등)는 근거의 기준값과 나란히 answer에 쓴다(예: '8명 중 2명이라 정확히 25%', '마감 45분 전이라 1시간 이내').
  수치가 조건을 충족하는지만 말하고 질문자의 수치를 빠뜨리지 않는다.
  결론을 가른 근거의 기준값('…이상'·'…이하'·'…대 이상' 등)은 숫자 그대로 answer에 쓴다. 질문자가 말한 금액·수량도 다시 쓴다.
  질문자의 경우에 적용한 기준값과 같은 항·호에 다른 경우(원칙, 다른 용도·유형)의 기준값이 나란히 있으면, check에서 두 값을 함께 적어 왜 질문자의 값이 적용되는지 비교하고,
  answer 끝에 '(…의 경우는 …)'처럼 다른 경우의 값을 괄호로 덧붙인다(예: '갑 유형이라 30%를 적용합니다(을 유형은 60%)').
  answer의 첫 단어(네/아니요)와 결론은 check의 결론과 같아야 한다. check에서 비교·계산에 쓴 근거 수치(감면율·금액·면적·비율 등)는 answer에도 그대로 적는다.
  질문에 나온 항목(여러 할인·요금·대상)이 있으면, 결론에 꼭 필요하지 않더라도 각 항목의 근거 수치를 answer에 함께 적고 그 수치의 근거 조항도 claims에 넣는다(예: '갑 감면 70%, 을 감면 30% 중 높은 하나만').
- 묻는 값이 조례 근거에 없을 때 '아니요, 그런 값은 없다'고 답하지 않는다. 아래 scope와 kind로 가린다.
- scope: 질문이 어느 규범을 묻는지 먼저 정한다. 서울특별시 조례의 내용을 묻으면 "seoul"이다.
  자치구(○○구)나 다른 시·도의 조례·규칙을 묻거나, 국가 법령(○○법·시행령·시행규칙)의 기준·처벌을 묻거나, 조문을 지어내라고 요구하면 "other"이고 이때 kind는 "none"이다.
- stated: 질문이 묻는 것(값·기준·가부)의 답이 조례 근거에 직접 적혀 있으면 true다. '근거가 있나요', '되나요'에 조례 조항이 그렇다고 정했으면 true이고,
  묻는 값이 조례에 없어 다른 규정에 맡겼거나 아예 없으면 false다. stated가 true이면 kind는 answer다.
- kind는 넷 중 하나다.
  "answer": 질문이 묻는 내용(값·기준·가부)이 조례 근거에 적혀 있다.
  답이 조건(원칙과 예외, 이용자·차량 유형 등)에 따라 갈리거나 질문이 짧아 대상이 하나로 정해지지 않아도, 묻는 내용을 정한 규정이 근거에 있으면 answer이고
  원칙부터 경우를 나눠 답한다(예: '원칙적으로 무료이고, 특별한 사유가 있으면 1건 700원이라 3건이면 2,100원').
  짧은 질문(예: '주차 감면')에는 대표적인 경우 2~4개와 그 수치를 요약한다.
  '근거가 있나요', '받을 수 있나요', '해도 되나요'처럼 가부를 묻는 질문에 조례 조항이 그 가부를 정했으면 answer다.
  그 제도의 세부 운영을 시장에게 맡긴 조항이 옆에 있어도 질문이 그 세부를 묻지 않았으면 answer이고, 맡긴 조항은 claims에 넣지 않는다.
  "delegated": 질문의 대상을 직접 다루는 조례 조항이 근거에 있고, 그 조항이 질문이 묻는 세부 내용(금액·비율·기준·크기·시간·절차 등)을
  "시장이 따로 정한다", "규칙으로 정한다", "…고시를 따른다", "…조례로 따로 정한다", "…령 별표와 같다"처럼 시장·규칙·고시·다른 법령에 맡겼다.
  "topic": 질문의 대상(그 지원·요금·사용료·수당·배상·제도) 그 자체를 두거나 시행하도록 정한 조례 조항이 근거에 있지만,
  묻는 값(금액·한도·기준 등)은 조례에 없고 맡긴다는 문구도 없다(예: '청년 월세 지원금이 얼마'↔'차임의 보조 방안을 수립·시행하여야 한다', '화장비가 얼마'↔'사용료를 부과할 수 있다').
  "none": 조례 근거에 질문의 대상을 두는 조항이 없다. 주제만 비슷한 조항, 다른 종류의 돈이나 제도를 정한 조항(보상금을 묻는데 요금 반환 조항뿐),
  질문이 전제한 사정(시스템 장애, 사고 등)을 다루지 않는 조항, 연락처·전화번호 같은 안내 정보가 모두 none이다.
  묻는 값을 조례에서 확인할 수 없다는 것만으로는 none이 아니다. 질문의 대상(그 과태료·요금·지원 등)을 다루는 조례 조항이 근거에 있으면,
  질문이 '조례 안에서 알려 달라'고 했더라도 값을 다른 법령·규칙에 맡겼으면 delegated, 맡긴다는 문구가 없으면 topic이다.
  조례 조항이 값을 맡긴 상위법·시행령 별표의 수치가 참고 근거에 있어도 그 수치는 쓰지 않고 delegated로 안내한다.
- delegated의 answer는 "조례에는 (질문이 묻는 것)이 정해져 있지 않습니다. (조례 이름) 제N조 제M항에 따라 (누가·무엇으로) 정하므로, 구체적인 내용은 서울시 안내에서 확인해 주세요" 꼴로 쓴다.
  topic의 answer는 "조례에는 (질문이 묻는 것)이 정해져 있지 않습니다. (조례 이름) 제N조 제M항은 (그 조항이 정한 내용)이라고만 정하므로, 구체적인 내용은 서울시 안내에서 확인해 주세요" 꼴로 쓴다.
  둘 다 "네/아니요"로 시작하지 않는다. 구체 값은 지어내지 않고 참고 근거에 있는 수치도 쓰지 않는다.
  claims에는 그 조항만 넣는다(질문자의 자격·대상을 정한 항이 따로 있으면 그것까지만).
- none이면 answer는 "확인 불가", claims는 빈 배열로 한다.
- answer에 '근거 1'처럼 근거 번호를 쓰지 않는다. 시민은 번호를 볼 수 없으므로 조항 이름(예: 제7조 제1항, 별표 1)으로 쓴다.
- 별표의 표는 머리글이 여러 줄이면 위 줄과 아래 줄을 합친 것이 칸 이름이다(예: '노외 주차장 / 월 정기권 / 전 일'은 노외주차장 월 정기권 중 전일권).
  질문의 '하루·하루 종일·1일' 요금은 '1일 주차요금'이나 '1일주차권' 칸이고, '전일·야간'은 월 정기권의 종류다. 답에는 고른 칸 이름을 함께 쓴다.
- '(참고·인용 불가)'로 표시된 근거는 조례가 따르는 상위법·시행령·시행규칙이다. 조례 조항의 뜻(정의·위임·용어)을 이해하는 데만 쓰고,
  claims의 evidence_id는 조례 근거에서 고른다.
- 자치구(OO구) 조례나 규칙은 근거로 쓰지 않는다.
결과는 JSON으로 낸다."""

SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "law_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "given": {"type": "array", "items": {"type": "string"}},
                "check": {"type": "string"},
                "stated": {"type": "boolean"},
                "scope": {"type": "string", "enum": ["seoul", "other"]},
                "kind": {"type": "string", "enum": ["answer", "delegated", "topic", "none"]},
                "answer": {"type": "string"},
                "claims": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"statement": {"type": "string"}, "evidence_id": {"type": "integer"}},
                    "required": ["statement", "evidence_id"],
                    "additionalProperties": False}},
            },
            "required": ["given", "check", "stated", "scope", "kind", "answer", "claims"],
            "additionalProperties": False,
        },
    },
}


ANNEX_NUM = re.compile(r"\d[\d,]*")


def annex_number_gaps(claims, hits):
    """별표를 근거로 든 주장의 숫자가 그 별표 원문에 실제로 있는지 봅니다(2주차 강의 '요금표 칸 검증').

    별표 요금은 칸을 잘못 읽으면 한 칸 옆 숫자를 말하게 됩니다. 원문에 없는 숫자를 돌려줍니다.
    """
    full = {}
    for ch in _load()[0]:   # 검색된 조각만이 아니라 그 별표 전체와 대조합니다
        full[ch["label"]] = full.get(ch["label"], "") + "\n" + ch["text"]
    gaps = []
    for c in claims:
        i = c.get("evidence_id", 0)
        if not 1 <= i <= len(hits) or "별표" not in hits[i - 1]["label"]:
            continue
        have = {n.replace(",", "") for n in ANNEX_NUM.findall(full.get(hits[i - 1]["label"], hits[i - 1]["text"]))}
        gaps += [n for n in ANNEX_NUM.findall(c["statement"]) if n.replace(",", "") not in have]
    return gaps


CHECK_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "self_check",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "supported": {"type": "array", "items": {"type": "boolean"}},
                "missing": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["supported", "missing"],
            "additionalProperties": False,
        },
    },
}

CHECK_SYSTEM = """너는 조례 답변 검토자다. 질문, 답, 주장마다 붙은 근거 원문을 받는다.
1. supported: 주장 순서대로, 그 주장이 붙은 근거 원문에 직접 적혀 있으면 true, 아니면 false.
2. missing: 질문이 물었는데 답이 답하지 않은 물음을, 조례에서 찾을 검색어 형태로 적는다(최대 2개). 다 답했으면 빈 배열.
결과는 JSON으로 낸다."""


def self_check(question, out, hits):
    """주장별 뒷받침 확인과 질문 부분 충족 확인을 한 번의 호출로 합니다(2주차 강의 자체 평가·검증)."""
    import upstage
    lines = []
    for n, c in enumerate(out.get("claims", []), 1):
        i = c.get("evidence_id", 0)
        src = hits[i - 1] if 1 <= i <= len(hits) else {"label": "없음", "text": ""}
        lines.append(f"[주장 {n}] {c['statement']}\n  근거: {src['label']}\n  {src['text'][:1200]}")
    raw = upstage.chat(
        [{"role": "system", "content": CHECK_SYSTEM},
         {"role": "user", "content": f"[질문]\n{question}\n\n[답]\n{out.get('answer', '')}\n\n" + "\n\n".join(lines)}],
        response_format=CHECK_SCHEMA)
    return json.loads(raw)


def _generate(question, hits, note=None):
    import upstage
    user = f"[근거]\n{_context(hits)}\n\n[질문]\n{question}"
    if note:
        user += f"\n\n[주의]\n{note}"
    def ask():
        return upstage.chat(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            response_format=SCHEMA, reasoning_effort=config.ANSWER_REASONING or None)

    for _ in range(2):   # 답이 중간에 잘려 JSON이 깨지면 다시 부릅니다(dev D09). 두 번 연달아 깨진 적이 있어 세 번까지(X03, 2026-11-19)
        try:
            return _consistent(json.loads(ask()))
        except json.JSONDecodeError:
            pass
    return _consistent(json.loads(ask()))


def _consistent(out):
    if out["stated"] and out["kind"] in ("delegated", "topic"):
        out["kind"] = "answer"
    return out


def _context(hits):
    return "\n\n".join(f"[근거 {i}] {'(참고·인용 불가) ' if h.get('ref') else ''}{h['label']}\n{h['text']}"
                       for i, h in enumerate(hits, 1))


NUM_TOKEN = re.compile(r"\d+(?:[.,]\d+)*")
ARTICLE_NO = re.compile(r"제\s?\d+\s?(조(의\s?\d+)?|항|호)|별표\s?\d+|별도\s?\d+")


def _num_tokens(text):
    """조·항 번호를 뺀 두 자리 이상 수치 집합('1,500' = '1500')."""
    return {t.replace(",", "") for t in NUM_TOKEN.findall(ARTICLE_NO.sub(" ", text)) if len(t.replace(",", "")) >= 2}


def reference_only_numbers(answer, question, hits):
    """답의 수치 중 조례 근거·질문에는 없고 상위법·시행규칙 참고 근거에만 있는 것.

    참고 근거는 인용할 수 없으므로, 그것에만 있는 값으로 답하면 조례 조항을 인용해도 근거 없는 답입니다
    (2026-11-19 함정 H39: 안내표지판 크기를 조례 제8조 제3항을 인용하며 시행규칙 별표의 210㏝×160㏝로 답함).
    """
    ref = _num_tokens(" ".join(h["text"] for h in hits if h.get("ref")))
    if not ref:
        return set()
    own = _num_tokens(" ".join(h["label"] + " " + h["text"] for h in hits if not h.get("ref")) + " " + question)
    return (_num_tokens(answer) & ref) - own


def reference_hits(question):
    """상위법·시행령·시행규칙 참고 조각(build_index.build_reference). DB가 없는 서버는 빈 목록."""
    if not (kb.ready("reference") or (config.DATA / "chunks_reference.json").exists()):
        return []
    try:
        return [{**h, "ref": True} for h in retrieve(question, config.REFERENCE_K, unit="reference")]
    except Exception:  # noqa: BLE001 참고는 덧붙이라, 고장 나도 조례 근거로 만드는 본 답변은 살립니다
        logging.getLogger("uvicorn.error").exception("참고 조각 검색 실패 - 참고 없이 답합니다")
        return []


def _original_texts():
    """시연 화면용: 인용 라벨 → 조·항 원문 전체(호 단위로 나누기 전)."""
    if "originals" not in _cache:
        texts = {}
        for law in json.loads((config.DATA / "ordinances.json").read_text(encoding="utf-8")):
            for a in law["articles"]:
                texts[f"{law['name']} {a['article']}"] = a["text"]
                for p in a["paragraphs"]:
                    if p["no"]:
                        texts[f"{law['name']} {a['article']} 제{p['no']}항"] = p["text"]
        _cache["originals"] = texts
    return _cache["originals"]


def answer(question):
    return answer_with_evidence(question)[0]


EVIDENCE_NO = re.compile(r"근거\s?(\d+)")
RATIO = re.compile(r"(\d[\d,]*)분의 ?(\d[\d,]*(?:\.\d+)?)")
SUBITEM = re.compile(r"(제\d+조(?:의\d+)?(?: ?제\d+항)?) ?제\d+호(?:의\d+)?(?: ?[가-하]목)?")


PERCENT_STATED_TWICE = re.compile(r"(\d[\d.]*)% ?\((?:\1%|\1 ?퍼센트)\)")
PARTICLE_AFTER_PERCENT = ((re.compile(r"%을(?![가-힣])"), "%를"), (re.compile(r"%은(?![가-힣])"), "%는"),
                          (re.compile(r"%이(?=\s)"), "%가"))


def _ratio_as_percent(m):
    denominator, numerator = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
    if denominator not in (100, 1000):
        return m.group(0)
    return f"{numerator / denominator * 100:g}%"


def plain_text(answer):
    answer = PERCENT_STATED_TWICE.sub(r"\1%", RATIO.sub(_ratio_as_percent, answer))
    for wrong, right in PARTICLE_AFTER_PERCENT:
        answer = wrong.sub(right, answer)
    return SUBITEM.sub(r"\1", answer)


def name_evidence(answer, hits):
    """답에 남은 내부 번호('근거 1')를 조항 이름으로 바꿉니다. 프롬프트로 막아도 남던 표현입니다(답 203개 중 27개, 11/19).

    '근거 2(제15조의2 제1항)'→'제15조의2 제1항', '근거 1의 제7조…'→'제7조…', 그 밖의 '근거 N'→N번 근거의 조례명·조항.
    """
    def label(m):
        i = int(m.group(1))
        return hits[i - 1]["label"].replace("서울특별시 ", "") if 1 <= i <= len(hits) else m.group(0)

    answer = re.sub(r"근거\s?\d+\s*\(([^)]*)\)", r"\1", answer)
    answer = re.sub(r"근거\s?\d+(?:의|에서)?\s*(?=제\s?\d|별표)", "", answer)
    return EVIDENCE_NO.sub(label, answer)


def answer_with_evidence(question):
    """대회 응답과 함께, 시연 화면에 보여 줄 인용 근거 원문 목록을 돌려줍니다.

    검색은 됐는데 답 단계에서 '확인 불가'로 포기하면 한 번만 처음부터 다시 답합니다(ABSTAIN_RETRY).
    temperature 0에서도 같은 문항이 실행마다 답↔포기로 뒤집혔기 때문입니다(X15·X16·H27, 11/19).
    다시 답해도 검증 ②~④를 그대로 거치므로 함정 문항에 지어낸 답이 통과하지는 않습니다.
    검색 점수가 기준선 아래라 막힌 경우(검증 ①)는 다시 묻지 않습니다.
    """
    result, evidence, retry = _answer_once(question)
    if retry and config.ABSTAIN_RETRY:
        result, evidence, _ = _answer_once(question)
    return result, evidence


GUIDE_KINDS = ("delegated", "topic")
YES_NO_HEAD = re.compile(r"^(네|아니요)[,.]?\s*")


def _cited_clauses(out, hits):
    claims = out["claims"]
    cited = refine_paragraphs(claims, hits) if config.USE_REFINE else [
        hits[c["evidence_id"] - 1] for c in claims if 1 <= c["evidence_id"] <= len(hits)]
    refs = [h for h in cited if h.get("ref")]
    return [h for h in cited if not h.get("ref") and not DISTRICT.search(h["label"])], refs


def _same_law_search(term, laws, seen):
    return [h for h in complete_articles(retrieve(term, top_k=6))
            if h["label"] not in seen and not h.get("ref") and h["law"] in laws and not DISTRICT.search(h["label"])]


def _delegating_clauses(question, cited, hits):
    laws = {h["law"] for h in cited}
    strong = [h for h in hits if not h.get("ref") and h["law"] in laws and DELEGATES_STRONG.search(h["text"])]
    cited_delegating = [h for h in cited if DELEGATES.search(h["text"])]
    cands = list({h["label"]: h for h in strong + cited_delegating}.values())
    picked, target = pick_delegating(question, cands) if cands else ([], "")
    if target:
        more = _same_law_search(target, laws, {h["label"] for h in hits})
        delegating = [h for h in more if DELEGATES_STRONG.search(h["text"])]
        if delegating:
            again, _ = pick_delegating(question, cands + delegating)
            if again:
                return again, hits + more
    return picked, hits


def _delegating_among_cited(cited, hits):
    articles = {_node_of(h["label"]) for h in cited}
    in_cited_articles = [h for h in hits if not h.get("ref") and _node_of(h["label"]) in articles
                         and DELEGATES_STRONG.search(h["text"])]
    return in_cited_articles or [h for h in cited if DELEGATES.search(h["text"])]


def _establishing_clauses(question, cited, hits):
    articles = {_node_of(h["label"]) for h in cited}
    siblings = [h for h in hits if not h.get("ref") and _node_of(h["label"]) in articles]
    cands = list({h["label"]: h for h in cited + siblings}.values())[:6]
    if not cands:
        return [], hits
    picked, term = clauses_establishing(question, cands)
    if picked or not term:
        return picked, hits
    more = _same_law_search(term, {h["law"] for h in cited}, {h["label"] for h in cands})[:6]
    if not more:
        return [], hits
    picked, _ = clauses_establishing(question, more)
    return picked, hits + [h for h in more if h["label"] not in {x["label"] for x in hits}]


def _guide_clauses(question, kind, cited, hits):
    picked, hits = _delegating_clauses(question, cited, hits)
    if picked or kind == "none":
        return picked, hits
    if kind == "delegated":
        picked = _delegating_among_cited(cited, hits)
        if picked:
            return picked, hits
    return _establishing_clauses(question, cited, hits)


def _answer_once(question):
    import upstage
    hits = retrieve_expanded(question)
    if config.USE_VERIFY and hits and hits[0]["cos"] is not None and hits[0]["cos"] < config.MIN_SCORE:
        return dict(UNKNOWN), [], False
    if config.USE_REFERENCE:
        hits = hits + reference_hits(question)

    out = _generate(question, hits)
    if config.USE_ANNEX_NUMCHECK and out["kind"] == "answer":
        gaps = annex_number_gaps(out["claims"], hits)
        if gaps:
            out = _generate(question, hits, f"숫자 {', '.join(gaps)}는 인용한 별표 원문에 없습니다. 별표의 해당 칸 숫자를 그대로 쓰라.")
    if out["kind"] != "none":
        leaked = reference_only_numbers(out["answer"], question, hits)
        if leaked:
            out = _generate(question, hits, f"숫자 {', '.join(sorted(leaked))}는 인용할 수 없는 참고 근거에만 있습니다. 답에 쓰지 말라. "
                                            "조례 조항이 그 내용을 규칙·고시 등에 맡겼으면 kind를 delegated로 하라.")
    if config.USE_SELF_CHECK and out["kind"] == "answer" and out["claims"]:
        chk = self_check(question, out, hits)
        if config.USE_SELF_RAG and chk["missing"]:
            seen = {h["label"] for h in hits}
            extra = [h for m in chk["missing"][:2] for h in retrieve(m, top_k=3) if h["label"] not in seen]
            extra = list({h["label"]: h for h in extra}.values())
            if extra:
                hits = hits + extra
                out = _generate(question, hits)
        else:
            ok = chk["supported"]
            kept = [c for c, s in zip(out["claims"], ok) if s]
            if config.USE_CLAIM_CHECK and kept and len(ok) == len(out["claims"]):
                out["claims"] = kept
    if out["scope"] != "seoul":
        return dict(UNKNOWN), [], True

    used, refs = _cited_clauses(out, hits)
    if out["kind"] != "answer" and used:
        cited_labels = {h["label"] for h in used}
        used, hits = _guide_clauses(question, out["kind"], used, hits)
        if used and (out["kind"] == "none" or {h["label"] for h in used} != cited_labels):
            names = ", ".join(h["label"] for h in used)
            again = _generate(question, hits, f"질문의 대상을 다루는 조례 조항은 {names}이다. 이 조항만 근거로 kind의 answer 꼴대로 안내하라. "
                                                "다른 조항을 근거로 쓰지 말고, 참고 근거의 수치도 쓰지 말라.")
            if again["kind"] in GUIDE_KINDS:
                out = again
    if not used or out["kind"] == "none":
        return dict(UNKNOWN), [], True
    if reference_only_numbers(out["answer"], question, hits):
        return dict(UNKNOWN), [], True
    if config.USE_VERIFY and upstage.groundedness(_context(used + refs), out["answer"]) == "notGrounded":
        return dict(UNKNOWN), [], True

    citation = list(dict.fromkeys(h["label"] for h in used))[:MAX_CITATIONS]
    used = [h for h in used if h["label"] in citation]
    originals = _original_texts()
    evidence = list({h["label"]: {"label": h["label"], "text": originals.get(h["label"], h["text"]),
                                   "source_url": h["source_url"],
                                   "effective_date": h["effective_date"]} for h in used}.values())
    text = name_evidence(out["answer"], hits)
    if config.USE_PLAIN:
        text = plain_text(text)
    text = fix_yes_no(question, text) if out["kind"] == "answer" else YES_NO_HEAD.sub("", text, count=1)
    return {"answer": text, "citation": citation,
            "source_url": used[0]["source_url"], "effective_date": used[0]["effective_date"]}, evidence, False


# ------------------------------------------------------------ 지식그래프 (시연 화면용)
# 채점용 /ask의 citation에는 영향을 주지 않습니다. 그래프로 넓힌 조문은 화면의 '연관 조례'에만 보여 줍니다.
def _graph():
    if "graph" not in _cache:
        _cache["graph"] = kb.load_graph()
    return _cache["graph"]


def _node_of(label):
    """인용 라벨(항 단위) → 그래프 노드(조·별표 단위)."""
    m = ARTICLE.match(label)
    return m.group(1) if m else label


def _node_vecs():
    """조·별표 노드마다 그 조각 벡터의 평균. 연관 조문을 질문과 가까운 순서로 고를 때 씁니다."""
    key = f"node_vecs_{config.CHUNK_UNIT}"
    if key not in _cache:
        chunks, vecs = _load()[:2]
        groups = {}
        for i, c in enumerate(chunks):
            groups.setdefault(_node_of(c["label"]), []).append(i)
        mat = None
        if vecs is not None:
            mat = np.stack([vecs[ids].mean(0) for ids in groups.values()])
            mat /= np.linalg.norm(mat, axis=1, keepdims=True)
        _cache[key] = (list(groups), mat)
    return _cache[key]


def _short_law(law):
    """'서울특별시 자전거이용 활성화에 관한 조례' → '자전거'"""
    first = law.replace("서울특별시", "").split()[0]
    return re.sub(r"(이용|환경|설치)$", "", first)


# 관계 이름(영문, DB) → 화면에 쓰는 한국어. kb.py 맨 위 설명과 같은 순서입니다.
REL_KO = {"HAS_ARTICLE": "조항", "HAS_ANNEX": "별표", "REFERS": "참조", "EXCEPTION_TO": "예외",
          "PENALIZES": "위반 시 제재", "APPLIES": "준용", "IS_LEGAL_BASIS_OF": "상위법 근거", "DELEGATES": "위임",
          "DEFINES": "용어 정의", "USES_TERM": "용어 사용", "BENEFITS": "혜택", "OBLIGES": "의무·제재",
          "ABOUT": "주제", "근거": "답변 근거", "주제": "질문 주제", "대상": "질문 대상",
          "AMENDS": "개정", "INTRODUCES": "신설", "ABROGATES": "삭제"}
REVISION_RELS = ("AMENDS", "INTRODUCES", "ABROGATES")
LINK_ORDER = ("EXCEPTION_TO", "PENALIZES", "APPLIES", "REFERS")   # 같은 조례 안 연결: 의미가 센 것부터


def related_graph(question, citation, limit=6, per_law=2):
    """질문·근거 조항에서 지식그래프를 타고 다른 조례의 연관 조문까지 넓혀, 화면에 그릴 부분 그래프를 돌려줍니다.

    1. 시작점: 답의 근거 조항. 근거가 없는 '확인 불가' 답에는 그래프를 만들지 않습니다(None) — 검색에만 걸린
       조례를 그리면 시민이 관련 규정이 있다고 오해할 수 있어서입니다.
    2. 질문에서 그래프 개체 고르기: 질문에 나온 대상자(장애인·청년 …)와 주제(건강·과태료 …)
    3. 다른 조례로 넓히기(경로마다 점수와 이유를 남김)
       대상자  같은 사람에게 혜택·의무를 주는 조문(BENEFITS/OBLIGES)   +0.15, 질문에 나온 대상이면 +0.1
       용어    시작점이 정의하거나 쓰는 용어를 함께 쓰는 조문(DEFINES/USES_TERM, 들어오는 방향도)  +0.12
       주제    같은 주제 조문(ABOUT)                                    +0.05 × 겹친 수, 질문 주제면 +0.1
       여기에 질문과 조문의 의미 유사도를 더해 순서를 정합니다(다른 조례만, 조례당 per_law개).
    4. 같은 조례 안: 예외·위반 시 제재·준용·참조(나가는 방향과 들어오는 방향 모두), 정의 조항, 상위법, 위임
    모든 선에는 원문 근거(reason)를 붙여 '어떤 법과 어떤 법이 왜 이어졌는지' 보이게 합니다.
    """
    g = _graph()
    nodes, note = g["nodes"], g["note"]
    seeds = [n for n in dict.fromkeys(_node_of(c) for c in citation) if n in nodes]
    if not seeds:
        return None

    def out(n, rels):
        return [d for rel, d in g["out"].get(n, []) if rel in rels]

    def into(n, rels):
        return [(rel, s) for rel, s in g["into"].get(n, []) if rel in rels]

    q = re.sub(r"\s", "", question)

    def mentioned(name):
        return any(len(w) >= 2 and w in q for w in re.split(r"[·\s]", name))

    # 행정 절차 주제는 거의 모든 조문에 붙어 조례 사이 다리로 쓰면 연결이 흐려집니다('공원 이용자' 같은 역할 대상은
    # 같은 이유로 kb.TARGET_PATTERNS에서 뺐습니다).
    generic = {f"주제:{t}" for t in kb.GENERIC_TOPICS}
    asked_topics = {f"주제:{t}" for t in kb.TOPICS if mentioned(t)}
    asked_targets = {f"대상:{t}" for t in kb.TARGETS if mentioned(t)} - generic
    topics = asked_topics | {t for s in seeds for t in out(s, ("ABOUT",)) if t not in generic}
    seed_targets = {t for s in seeds for t in out(s, ("BENEFITS", "OBLIGES"))} - generic
    targets = asked_targets or seed_targets
    terms = {t for s in seeds for t in out(s, ("DEFINES", "USES_TERM"))}
    seed_laws = {nodes[s]["law"] for s in seeds}

    ids, mat = _node_vecs()
    sim = dict(zip(ids, mat @ _query_vec(question))) if mat is not None and config.UPSTAGE_API_KEY else {}
    cand = {}   # 조문 → [점수, 경로 목록[(관계, 거쳐 간 개체)]]

    def offer(n, bonus, rel, via):
        if n in seeds or nodes[n]["law"] in seed_laws or nodes[n]["kind"] not in ("article", "annex"):
            return
        c = cand.setdefault(n, [float(sim.get(n, 0.0)), []])
        c[0] += bonus
        c[1].append((rel, via))

    for t in targets:
        for rel, n in into(t, ("BENEFITS", "OBLIGES")):
            offer(n, 0.15 + (0.1 if t in asked_targets else 0), rel, t)
    for t in terms:
        for rel, n in into(t, ("DEFINES", "USES_TERM")):
            offer(n, 0.12, rel, t)
    for t in topics:
        for rel, n in into(t, ("ABOUT",)):
            offer(n, 0.05 + (0.1 if t in asked_topics else 0), rel, t)
    related, per = [], Counter()
    for n, (score, paths) in sorted(cand.items(), key=lambda kv: -kv[1][0]):
        if per[nodes[n]["law"]] < per_law and len(related) < limit:
            related.append(n)
            per[nodes[n]["law"]] += 1

    out_nodes, edges = {}, {}

    def add(n, role):
        if n in out_nodes:
            return
        info = nodes[n]
        kind = info["kind"]
        if kind in ("topic", "law", "target", "delegation"):
            name = info["name"]
        elif kind == "revision":
            name = info["name"].replace("-", ".") + " 개정"
        elif kind == "term":
            name = f"“{info['name']}”"
        else:
            name = _short_law(info["law"]) + ("" if kind == "ordinance" else " " + info["name"])
        out_nodes[n] = {"id": n, "kind": kind, "role": role, "name": name, "title": info["title"], "law": info["law"]}
        if kind in ("article", "annex"):
            add(info["law"], "ordinance")
            edge(info["law"], "HAS_ARTICLE" if kind == "article" else "HAS_ANNEX", n)

    def edge(s, rel, d, reason=None):
        edges.setdefault((s, rel, d), reason if reason is not None else note.get((s, rel, d), ""))

    out_nodes["질문"] = {"id": "질문", "kind": "question", "role": "question", "name": "질문", "title": question, "law": ""}
    for s in seeds:
        add(s, "cited")
        edge("질문", "근거", s, "")
    reasons = {}
    for n in related:
        add(n, "related")
        for rel, via in cand[n][1]:
            if via.startswith("주제:") and len(cand[n][1]) > 1 and any(not v.startswith("주제:") for _, v in cand[n][1]):
                continue   # 대상자·용어로 이어진 조문은 주제 선을 생략해 그림을 단순하게
            add(via, nodes[via]["kind"])
            edge(n, rel, via)
            for s in seeds:
                for srel in ("BENEFITS", "OBLIGES", "DEFINES", "USES_TERM", "ABOUT"):
                    if (s, srel, via) in note:
                        edge(s, srel, via)
            label = nodes[via]["name"]
            why = str(note.get((n, rel, via)) or "")
            why = why if len(why) <= 60 else why[:58] + "…"
            reasons.setdefault(n, []).append(
                f"같은 대상({label}) {why}" if rel in ("BENEFITS", "OBLIGES") else
                f"용어 ‘{label}’ {'정의' if rel == 'DEFINES' else '사용'}: {why}" if rel in ("DEFINES", "USES_TERM") else
                f"같은 주제 #{label}")
    for t in asked_topics | asked_targets:
        if t in out_nodes:
            edge("질문", "대상" if t.startswith("대상:") else "주제", t, "")

    picked = 0
    for s in seeds:   # 같은 조례 안의 예외·제재·준용·참조(양방향)
        links = [(rel, s, d) for rel, d in g["out"].get(s, []) if rel in LINK_ORDER]
        links += [(rel, src, s) for rel, src in g["into"].get(s, []) if rel in LINK_ORDER]
        for rel, a_, b_ in sorted(links, key=lambda x: LINK_ORDER.index(x[0])):
            other = b_ if a_ == s else a_
            if other in out_nodes:
                edge(a_, rel, b_)
            elif picked < 4 and nodes[other]["kind"] in ("article", "annex", "ordinance"):
                add(other, "ordinance" if nodes[other]["kind"] == "ordinance" else "reference")
                edge(a_, rel, b_)
                picked += 1
        for t in out(s, ("USES_TERM",)):   # 시작점이 쓰는 용어의 정의 조항
            for _, dfn in into(t, ("DEFINES",)):
                if nodes[dfn]["law"] in seed_laws and dfn not in out_nodes and picked < 5:
                    add(t, "term")
                    add(dfn, "reference")
                    edge(s, "USES_TERM", t)
                    edge(dfn, "DEFINES", t)
                    picked += 1
        for rel, src in into(s, ("IS_LEGAL_BASIS_OF",))[:2]:
            add(src, "law")
            edge(src, rel, s)
        for d in out(s, ("DELEGATES",))[:1]:
            add(d, "delegation")
            edge(s, "DELEGATES", d)
        latest_revision = sorted(into(s, REVISION_RELS), key=lambda x: nodes[x[1]]["name"], reverse=True)[:1]
        for rel, src in latest_revision:
            add(src, "revision")
            edge(src, rel, s)

    chunks = _load()[0]
    url = {c["law"]: c["source_url"] for c in chunks}
    originals = _original_texts()
    related_list = [{"label": n, "law": nodes[n]["law"], "title": nodes[n]["title"],
                     "topics": sorted(nodes[v]["name"] for _, v in cand[n][1] if v.startswith("주제:")),
                     "reasons": list(dict.fromkeys(reasons.get(n, []))),
                     "text": originals.get(n, ""), "source_url": url.get(nodes[n]["law"], "")} for n in related]
    return {"nodes": list(out_nodes.values()),
            "edges": [{"src": s, "rel": r, "dst": d, "label": REL_KO.get(r, r), "reason": why}
                      for (s, r, d), why in sorted(edges.items())],
            "related": related_list}


def answer_without_rag(question):
    """시연용 '전' 장면: 검색 없이 LLM만으로 답하게 하면 어떻게 되는가."""
    import upstage
    return upstage.chat([
        {"role": "system", "content": "너는 서울시 조례 전문가다. 답과 함께 근거 조항(제○조 제○항)과 원문 링크, 시행일을 적어라."},
        {"role": "user", "content": question}], temperature=0.3)
