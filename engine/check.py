"""셀프 채점기: 공개 예시 문항으로 대회 채점 방식을 흉내 냅니다.

  근거 정확도  항까지 일치 1.0 · 조까지만 일치 0.5 · 별표 인용도 정답 인정
  함정 문항    answer가 "확인 불가"이고 citation이 []이면 정답
  위임·준용    조례가 세부를 시장·규칙·고시에 맡긴 문항은 그 조항을 인용해야 정답(확인 불가는 0점, 공지 2026-11-19)
  인용 초과    인용이 정답 근거 수보다 2개 넘게 많으면 넘친 비율만큼 감점
  형식         source_url · effective_date가 비어 있지 않은지, 90초 안에 응답했는지

실행:
  python -X utf8 check.py --retrieval      검색만 점검 (API 키 없이도 동작)
  python -X utf8 check.py                  로컬 함수로 전체 채점
  python -X utf8 check.py --url http://localhost:8000/ask   서버로 채점 (제출 전 최종 점검)

※ 실제 채점 규칙의 세부(인용을 여러 개 냈을 때의 처리 등)는 운영진 공지가 기준입니다.
"""
import argparse
import json
import re
import time
from datetime import datetime

import config

CITE = re.compile(r"(서울특별시.*?(?:조례|규칙))\s*(제\d+조(?:의\d+)?|별표\s*\d*|별도\s*\d+)\s*(제\d+항)?")


def parse(c):
    m = CITE.search(c.replace(" ", " "))
    if not m:
        return None
    law = re.sub(r"\s+", "", m.group(1))
    art = re.sub(r"\s+", "", m.group(2))
    para = m.group(3)
    return law, art, para


def match(pred, gold):
    p, g = parse(pred), parse(gold)
    if not p or not g or p[0] != g[0] or p[1] != g[1]:
        return 0.0
    if g[2] is None or p[2] == g[2]:
        return 1.0
    return 0.5


def score_citation(preds, gold):
    """gold: [[대안1, 대안2], [다음 필수 근거], ...]

    인용 개수가 정답 근거 수보다 2개 넘게 많으면 넘친 비율만큼 깎습니다(운영진 공지 2026-11-19).
    예) 정답 1개에 인용 4개 → 1개가 넘쳐 점수 × 3/4.
    """
    if not gold:
        return None
    total = 0.0
    for alts in gold:
        total += max((match(p, a) for p in preds for a in alts), default=0.0)
    over = len(preds) - (len(gold) + 2)
    return total / len(gold) * (1 - over / len(preds) if over > 0 else 1)


NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(만|천)?")


def _numbers(text):
    """글에 나오는 수를 정규화한 집합. '10만'은 100000, '1,500'은 1500으로 같이 봅니다."""
    out = set()
    text = re.sub(r"제\s?\d+\s?(조(의\s?\d+)?|항|호)|법\s?제\s?\d+조|별표\s?\d+", " ", text)  # 조·항 번호는 답 내용이 아님
    # 법령 표기를 같은 수로: '100분의 50' = 50%, '10/1,000' = 1%, '1천500' = 1500, '1억' = 100000000
    # 비율 표기는 백분율 하나로 바꾸고 원문에서 지웁니다. 남겨 두면 분모(100, 1,000)까지 '요구 숫자'가 되어
    # '20%'로 맞게 답해도 '100분의 20'이라는 정답 요지와 어긋났습니다(11/19 스트레스 세트 X04·X37).
    def ratio(m, num, den):
        out.add(round(float(m.group(num).replace(",", "")) / float(m.group(den).replace(",", "")) * 100, 4))
        return " "
    text = re.sub(r"(\d[\d,]*)\s*분의\s*(\d[\d,]*)", lambda m: ratio(m, 2, 1), text)
    text = re.sub(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", lambda m: ratio(m, 1, 2), text)
    for a, b in re.findall(r"(\d+)\s*천\s*(\d+)", text):
        out.add(float(a) * 1000 + float(b))
    for a in re.findall(r"(\d+)\s*억", text):
        out.add(float(a) * 1e8)
    for n, unit in NUM.findall(text):
        v = float(n.replace(",", ""))
        v *= {"만": 10000, "천": 1000}.get(unit, 1)
        out.add(v)
    return out


def content_ok(answer, key):
    """답 내용 점검: 정답 요지(answer_key)의 수가 답에 모두 있고, 요지가 네/아니요로 시작하면 방향이 같은지.

    인용이 맞아도 금액이나 긍·부정을 틀리면 답변 품질(20점)에서 깎이므로 따로 잇니다
    (2주차 강의의 '다중 평가 기준'). 판단할 수 없는 요지(수도 네/아니요도 없음)는 None.
    """
    if not key:
        return None
    checks = []
    want = _numbers(key)
    if want:
        checks.append(want <= _numbers(answer))
    for word in ("아니요", "네"):
        if key.startswith(word):
            checks.append(answer.startswith(word))
            break
    return all(checks) if checks else None


def run_retrieval(qs):
    import rag
    print(f"[검색 점검] 단위={config.CHUNK_UNIT}  top_k={config.TOP_K}\n")
    hit_n = 0
    for q in qs:
        if not q["gold"]:
            hits = rag.retrieve(q["question"])
            print(f"{q['id']:3} 함정   top1={hits[0]['label']}  (score {hits[0]['score']:.2f})")
            continue
        labels = [h["label"] for h in rag.retrieve(q["question"])]
        sc = score_citation(labels, q["gold"])
        hit_n += sc
        mark = "✓" if sc == 1 else ("△" if sc > 0 else "✗")
        print(f"{q['id']:3} {mark} {sc:.2f}  {q['question'][:34]}")
        if sc < 1:
            print("      찾은 것:", labels[:3])
    n = sum(1 for q in qs if q["gold"])
    print(f"\n정답 근거가 top-{config.TOP_K} 안에 들어온 비율: {hit_n / n:.0%}")
    # 실제 답변은 2단계 검색(retrieve_expanded) 결과를 씁니다. 키가 있으면 그 포함률도 잽니다.
    if config.USE_EXPAND and config.UPSTAGE_API_KEY:
        exp = sum(score_citation([h["label"] for h in rag.retrieve_expanded(q["question"])], q["gold"])
                  for q in qs if q["gold"])
        print(f"실제 답변에 쓰는 2단계 검색에 정답 근거가 들어온 비율: {exp / n:.0%}")


def run_full(qs, url=None):
    if url:
        import requests
        call = lambda q: requests.post(url, json={"question": q}, timeout=90).json()  # noqa: E731
    else:
        import rag
        call = rag.answer
    rows, cite_scores, trap_ok, gray_ok = [], [], [], []
    for q in qs:
        t0 = time.time()
        try:
            r = call(q["question"])
            err = None
        except Exception as e:  # noqa: BLE001
            r, err = {}, repr(e)
        sec = time.time() - t0
        unknown = r.get("answer", "").startswith("확인 불가") and r.get("citation") == []
        if q["gold"]:
            sc = 0.0 if err or sec > 90 else score_citation(r.get("citation", []), q["gold"])
            cite_scores.append(sc)
        elif q.get("gray_gold"):   # 회색지대: 확인 불가도, 그 조항을 인용한 안내도 정답으로 봅니다
            sc = 0.0 if err or sec > 90 else 1.0 if unknown else score_citation(r.get("citation", []), q["gray_gold"])
            gray_ok.append(sc)
        else:
            sc = 1.0 if unknown and not err and sec <= 90 else 0.0
            trap_ok.append(sc)
        fmt_ok = unknown or (r.get("source_url", "").startswith("http") and
                             re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.get("effective_date", "")))
        rows.append({"id": q["id"], "type": q["type"], "question": q["question"], "score": sc,
                     "sec": round(sec, 1), "format_ok": bool(fmt_ok), "error": err, "response": r,
                     "gold": q["gold"], "answer_key": q["answer_key"]})
        print(f"{q['id']:3} {sc:.2f}  {sec:5.1f}s  {'형식OK' if fmt_ok else '형식✗'}  {r.get('citation')}")
    print("\n근거 정확도(일반 문항 평균):", f"{sum(cite_scores) / max(len(cite_scores), 1):.2f}")
    print("함정 문항 정답률:", f"{sum(trap_ok) / max(len(trap_ok), 1):.0%}")
    if gray_ok:
        print("회색지대 문항(확인 불가 또는 해당 조항 인용):", f"{sum(gray_ok) / len(gray_ok):.2f} ({len(gray_ok)}문항)")
    judged = [(row["id"], content_ok(row["response"].get("answer", ""), row["answer_key"]))
              for row in rows if row["gold"]]
    judged = [(i, ok) for i, ok in judged if ok is not None]
    if judged:
        miss = [i for i, ok in judged if not ok]
        print("답 내용 일치율(정답 요지의 수·네/아니요):",
              f"{1 - len(miss) / len(judged):.0%} ({len(judged)}문항)", f"빠진 문항: {miss}" if miss else "")
    out = config.ROOT / "results"
    out.mkdir(exist_ok=True)
    path = out / f"check_{datetime.now():%m%d_%H%M%S}_{config.CHUNK_UNIT}_v{int(config.USE_VERIFY)}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--retrieval", action="store_true")
    ap.add_argument("--url")
    ap.add_argument("--questions", default=str(config.ROOT / "sample_questions.json"))
    a = ap.parse_args()
    qs = json.loads(open(a.questions, encoding="utf-8").read())
    run_retrieval(qs) if a.retrieval else run_full(qs, a.url)
