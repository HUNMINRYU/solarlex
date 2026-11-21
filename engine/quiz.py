import json
import random
import re
import sys

import config
import kb

QUIZ_FILE = config.DATA / "quiz.json"
EXCLUDED_FILE = config.DATA / "quiz_excluded.json"
NUMBER = re.compile(r"\d[\d,]*")
MEASURED = re.compile(r"\d[\d,]*\s*(만원|천원|원|세|일|개월|년|시간|분|퍼센트|%|미터|명|회|배)|\d+분의\s*\d+")
CHOICES = ["O", "X"]

MAX_STATEMENT = 45

SYSTEM = (
    "너는 서울시 조례로 누구나 가볍게 푸는 생활 상식 OX 퀴즈를 만드는 사람이다. 주어진 조항 하나로 OX 문항 1개의 재료를 만든다.\n"
    "- 시민이 일상에서 겪을 만한 내용(요금, 할인, 과태료, 나이 기준, 이용 시간, 신청 기한 등)일 때만 만든다. "
    "위원회 구성·임기, 행정 계획, 위탁·허가 절차, 시설 설치 기준처럼 공무원이나 사업자에게만 해당하는 조항이면 true를 빈 문자열로 낸다.\n"
    "- true: 조항 내용과 맞는 문장 하나. 중학생도 바로 알아듣게, 친구에게 말하듯 짧은 해요체로 45자 안에 쓴다. "
    "조문을 옮겨 적지 말고 생활 속 장면으로 풀어 쓴다"
    "(예: '서울 금연구역에서 담배 피우다 걸리면 과태료 10만원이에요.', '서울에서 강아지는 태어난 지 2개월이 지나면 등록해야 하는 대상이에요.'). "
    "법령 용어(부과·징수, 감면, 단수, 경형자동차, 소유자등)는 일상 말(내야 해요, 깎아 줘요, 잔돈, 경차, 주인)로 바꾸고, "
    "조·항 번호와 조례 이름, '조례에 따르면' 같은 말은 쓰지 않는다.\n"
    "- false: true와 같은 장면에서 핵심 사실 하나(금액, 나이, 기간, 비율, 되는지 안 되는지)만 바꿔 분명히 틀린 말로 만든 문장. 말투와 길이는 true와 같게 쓴다. "
    "조항 원문과 견주면 누가 봐도 틀려야 한다. 범위가 붙은 값은 바꿔도 여전히 맞는 말이 되지 않게 고른다"
    "(원문이 '3회 이상 밀리면 거절할 수 있다'일 때 '5회 이상'은 여전히 맞으므로 쓰지 않는다).\n"
    "- 두 문장 모두 원문의 뜻을 바꾸지 않는다. '이상·이하·미만'의 경계, '할 수 있다'와 '해야 한다'의 차이, 누가 대상인지(아이 본인인지 함께 간 어른인지), "
    "원칙과 예외를 뒤섞지 않는다. 예외일 때만 해당하는 내용을 늘 그런 것처럼 쓰지 않는다. "
    "'이상'은 '7일 이상', '3번 이상'처럼 그대로 쓰고 '넘게', '넘으면'으로 바꾸지 않는다. "
    "원문이 '지정할 수 있다', '할 수 있다'이면 이미 그렇게 되어 있는 것처럼 쓰지 않고 '~할 수 있어요'로 쓴다.\n"
    "- explanation: 실제로는 어떻게 되어 있는지 쉬운 해요체로 한두 문장, 70자 안에 쓴다. 조항 원문에 있는 내용만 쓰고 비율은 %로 쓴다. "
    "법령 용어, 조·항 번호, 참·거짓·정답이라는 말은 쓰지 않는다(예: '금연구역에서 담배를 피우면 과태료 10만원을 내야 해요.').\n"
    "- 조항만으로 만들 수 없으면 true를 빈 문자열로 낸다. 결과는 JSON으로 낸다.")
SCHEMA = {"type": "json_schema", "json_schema": {"name": "quiz_item", "strict": True, "schema": {
    "type": "object", "required": ["true", "false", "explanation"], "additionalProperties": False,
    "properties": {"true": {"type": "string"}, "false": {"type": "string"}, "explanation": {"type": "string"}}}}}
ASK = ("퀴즈 문장이 다루는 내용을 시민이 서울시 조례 상담 창구에 물어볼 질문 한 문장으로 바꾼다. "
       "'서울'을 넣어 쉬운 말로 쓰고, 답이 되는 숫자는 넣지 않는다(예: '서울 금연구역에서 담배를 피우면 과태료가 얼마인가요?'). 결과는 JSON으로 낸다.")
ASK_SCHEMA = {"type": "json_schema", "json_schema": {"name": "quiz_ask", "strict": True, "schema": {
    "type": "object", "required": ["question"], "additionalProperties": False, "properties": {"question": {"type": "string"}}}}}
JUDGE = ("조항 원문만 근거로 문장을 판정한다. 문장이 원문 규정과 같거나, 숫자가 달라도 원문 규정에서 논리적으로 따라 나오는 말이면 O"
         "(원문이 '3회 이상이면 거부할 수 있다'일 때 '5회 이상이면 거부할 수 있다'는 O). "
         "원문 규정과 어긋나 반드시 틀린 말이면 X, 원문으로 가릴 수 없거나 읽기에 따라 달라지면 ?를 answer로 낸다. '이상'을 '넘게'로 쓴 것처럼 경계가 원문과 달라졌거나, '할 수 있다'를 이미 정해진 것처럼 쓴 문장은 ?로 낸다. 결과는 JSON으로 낸다.")
JUDGE_SCHEMA = {"type": "json_schema", "json_schema": {"name": "quiz_judge", "strict": True, "schema": {
    "type": "object", "required": ["answer"], "additionalProperties": False,
    "properties": {"answer": {"type": "string", "enum": ["O", "X", "?"]}}}}}


def _numbers(text):
    return {n.replace(",", "") for n in NUMBER.findall(text)}


def candidates():
    chunks, _ = kb.load_chunks("paragraph")
    by_label = {}
    for c in chunks:
        item = by_label.setdefault(c["label"], {**c, "text": ""})
        item["text"] = (item["text"] + "\n" + c["text"]).strip()
    reviewed = {r["article"] for r in json.loads(kb.TARGET_REVIEW.read_text(encoding="utf-8")) if r["keep"]}
    return [c for c in by_label.values() if MEASURED.search(c["text"]) or any(c["label"] == a or c["label"].startswith(a + " ") for a in reviewed)]


def _grounded(item, clause):
    true, false = item["true"].strip(), item["false"].strip()
    known = _numbers(clause["text"])
    return bool(true) and bool(false) and true != false and max(len(true), len(false)) <= MAX_STATEMENT \
        and _numbers(true) <= known and _numbers(item["explanation"]) <= known


def _judged(statement, clause):
    import upstage
    raw = upstage.chat([{"role": "system", "content": JUDGE}, {"role": "user", "content": f"[조항 원문]\n{clause['text']}\n\n[문장]\n{statement}"}],
                       response_format=JUDGE_SCHEMA)
    return json.loads(raw)["answer"]


def _ask(statement):
    import upstage
    return json.loads(upstage.chat([{"role": "system", "content": ASK}, {"role": "user", "content": statement}], response_format=ASK_SCHEMA))["question"].strip()


def _item(clause):
    import upstage
    raw = upstage.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"{clause['label']}\n{clause['text'][:1500]}"}],
                       response_format=SCHEMA, temperature=0.3)
    item = json.loads(raw)
    if not _grounded(item, clause):
        return None
    true, false = item["true"].strip(), item["false"].strip()
    if _judged(true, clause) != "O" or _judged(false, clause) != "X":
        return None
    return {"label": clause["label"], "law": clause["law"], "source_url": clause["source_url"], "effective_date": clause["effective_date"],
            "text": clause["text"], "true": true, "false": false, "explanation": item["explanation"].strip(), "ask": _ask(true)}


def build():
    from concurrent.futures import ThreadPoolExecutor
    excluded = json.loads(EXCLUDED_FILE.read_text(encoding="utf-8"))
    kept = [] if "--regen" in sys.argv or not QUIZ_FILE.exists() else \
        [item for item in json.loads(QUIZ_FILE.read_text(encoding="utf-8")) if item["label"] not in excluded]
    done = {item["label"] for item in kept}
    missing = [c for c in candidates() if c["label"] not in done and c["label"] not in excluded]
    with ThreadPoolExecutor(6) as pool:
        made = [item for item in pool.map(_item, missing) if item]
    QUIZ_FILE.write_text(json.dumps(kept + made, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"퀴즈 문항: 기존 {len(kept)}개 유지, 새 후보 {len(missing)}개 중 {len(made)}개 통과 → {QUIZ_FILE.name}에 {len(kept) + len(made)}개")
    return made


def pick(count):
    by_law = {}
    for item in json.loads(QUIZ_FILE.read_text(encoding="utf-8")):
        by_law.setdefault(item["law"], []).append(item)
    for items in by_law.values():
        random.shuffle(items)
    laws = random.sample(list(by_law), len(by_law))
    picked = []
    while len(picked) < count and any(by_law.values()):
        picked += [by_law[law].pop() for law in laws if by_law[law]]
    out = []
    for item in picked[:count]:
        truth = random.choice(["true", "false"])
        out.append({key: item[key] for key in ("label", "law", "source_url", "effective_date", "text", "explanation", "ask")}
                   | {"question": item[truth], "choices": CHOICES, "answer": 0 if truth == "true" else 1})
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    build()
