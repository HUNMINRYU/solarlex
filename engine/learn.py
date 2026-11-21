"""학습: 조례 원문으로 '예상 질문 색인'을 만들어 검색을 시민 말투에 맞춥니다.

왜 필요한가
  시민은 "아기 데리고 가면 가족 칸 써도 돼요?"처럼 묻고, 조문은 "임산부 및 영유아를 동반한 사람"이라고 씁니다.
  말투가 달라 조문 임베딩만으로는 거리가 멉니다. 그래서 조항마다 시민이 실제로 물을 법한 질문을
  Solar가 미리 만들어 두고(조례 원문이 학습 데이터), 들어온 질문을 이 예상 질문들과도 비교합니다.

과정 (모두 Upstage 모델만 씀)
  1. 조례 원문(data/ordinances.json, 법제처 OPEN API로 받은 6종)에서 항·별표 단위 조항을 꺼냄
  2. solar-pro4가 조항마다 시민 말투 예상 질문 3개 생성 → data/learned_questions.json (있는 조항은 재사용, 새 조항만 생성)
  3. solar-embedding-2-query로 임베딩 → data/solarlex.db의 questions 표(질문 색인)
  4. --tune: 개발셋(dev 60문항)으로 가중치를 고르고, 홀드아웃(40문항)으로 정말 나아지는지 확인

실행:  python -X utf8 learn.py            빠진 조항의 예상 질문 생성 + 질문 색인 저장
       python -X utf8 learn.py --regen    예상 질문을 새로 생성
       python -X utf8 learn.py --tune     가중치별 검색 포함률(dev·holdout) 표
검색에 쓰는 가중치는 config.QINDEX_WEIGHT(환경변수 QINDEX_WEIGHT)입니다.
"""
import json
import sys

import numpy as np

import config
import kb

QUESTION_FILE = config.DATA / "learned_questions.json"
PER_LABEL = 3

SYSTEM = (
    "너는 서울시 조례 상담 데이터를 만드는 사람이다. 주어진 조항이 정답 근거가 되는 시민 질문을 3개 만든다.\n"
    "- 조문 용어를 그대로 베끼지 말고 시민이 일상에서 쓰는 말로 쓴다(예: '영유아를 동반한' → '아기 데리고').\n"
    "- 세 질문은 서로 다른 상황·대상·말투로 쓴다. 하나는 짧게, 하나는 자기 상황을 설명하며 묻는다.\n"
    "- 조항에 없는 내용을 묻지 않는다. 결과는 JSON으로 낸다.")

SCHEMA = {"type": "json_schema", "json_schema": {"name": "learned_questions", "strict": True, "schema": {
    "type": "object", "required": ["questions"], "additionalProperties": False,
    "properties": {"questions": {"type": "array", "items": {"type": "string"}}}}}}


def clauses():
    """인용 라벨(항 단위) → 조항 원문. 같은 라벨의 조각은 이어 붙입니다."""
    chunks, _ = kb.load_chunks("paragraph")
    out = {}
    for c in chunks:
        out[c["label"]] = out.get(c["label"], "") + "\n" + c["text"]
    return out


def _questions(item):
    import upstage
    label, text = item
    raw = upstage.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"{label}\n{text.strip()[:900]}"}],
                       response_format=SCHEMA, temperature=0.3)
    return [q.strip() for q in json.loads(raw)["questions"] if q.strip()][:PER_LABEL]


def generate(items):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(6) as pool:
        return {label: questions for (label, _), questions in zip(items, pool.map(_questions, items))}


def build():
    import upstage
    learned = {} if "--regen" in sys.argv or not QUESTION_FILE.exists() else \
        json.loads(QUESTION_FILE.read_text(encoding="utf-8"))
    missing = [(label, text) for label, text in clauses().items() if label not in learned]   # 새로 들어온 조항만
    if missing:
        learned.update(generate(missing))
        QUESTION_FILE.write_text(json.dumps(learned, ensure_ascii=False, indent=1), encoding="utf-8")
    rows = [(label, q) for label, qs in learned.items() for q in qs]
    vecs = upstage.embed([q for _, q in rows], config.EMBED_QUERY)
    kb.save_questions(rows, vecs)
    print(f"질문 색인: 조항 {len(learned)}개, 예상 질문 {len(rows)}개를 {kb.DB.name}에 저장했습니다")


def tune(weights=(0.0, 0.05, 0.1, 0.15, 0.2, 0.3)):
    """가중치별 1단계 검색 top-k 포함률. 개발셋으로 고르고 홀드아웃은 확인만 합니다."""
    import check
    import rag
    sets = {name: json.loads((config.ROOT / "eval" / f"{name}_questions.json").read_text(encoding="utf-8"))
            for name in ("dev", "holdout")}
    print("가중치   " + "   ".join(f"{n}(n={sum(1 for q in qs if q['gold'])})" for n, qs in sets.items()))
    for w in weights:
        config.QINDEX_WEIGHT = w
        row = []
        for qs in sets.values():
            gold = [q for q in qs if q["gold"]]
            hit = sum(check.score_citation([h["label"] for h in rag.retrieve(q["question"])], q["gold"]) for q in gold)
            row.append(f"{hit / len(gold):.3f}")
        print(f"{w:<7}  " + "      ".join(row))


if __name__ == "__main__":
    tune() if "--tune" in sys.argv else build()
