import json
import random
import re
import sys

import config
import kb

QUIZ_FILE = config.DATA / "quiz.json"
EXCLUDED_FILE = config.DATA / "quiz_excluded.json"
CHOICES = 4
NUMBER = re.compile(r"\d[\d,]*")
MEASURED = re.compile(r"\d[\d,]*\s*(만원|천원|원|세|일|개월|년|시간|분|퍼센트|%|미터|명|회|배)|\d+분의\s*\d+")
CHOICES = ["O", "X"]

MAX_STATEMENT = 55

SYSTEM = (
    "너는 서울시 조례로 누구나 가볍게 푸는 생활 상식 OX 퀴즈를 만드는 사람이다. 주어진 조항 하나로 OX 문항 1개의 재료를 만든다.\n"
    "- 시민이 일상에서 겪을 만한 내용(요금, 할인, 과태료, 나이 기준, 이용 시간, 신청 기한 등)일 때만 만든다. "
    "위원회 구성·임기, 행정 계획, 위탁·허가 절차, 시설 설치 기준처럼 공무원이나 사업자에게만 해당하는 조항이면 statement를 빈 문자열로 낸다.\n"
    "- statement: 조항 내용과 일치하는 참인 문장 하나. 친구에게 말하듯 쉬운 해요체로 55자 안에 쓴다"
    "(예: '서울 금연구역에서 담배를 피우면 과태료가 10만원이에요.'). 법령 용어는 일상 말로 바꾸고"
    "(예: 부과·징수한다 → 내야 해요, 경형자동차 → 경차), 조·항 번호와 '조례에 따르면' 같은 말은 쓰지 않는다.\n"
    "- statement에는 조항 원문에 나오는 숫자 표현 하나가 정확히 한 번 들어가야 한다. 숫자는 원문과 같아야 하고 다른 숫자는 넣지 않는다. "
    "표기는 쉽게 바꿔도 된다(100분의 50 → 50%).\n"
    "- value: statement에 들어간 그 숫자 표현을 글자 그대로 옮긴 것(예: 10만원, 5일, 50%).\n"
    "- wrong_value: value와 같은 단위·형식의 그럴듯한 다른 값. 이 조항 원문 어디에도 나오지 않는 숫자여야 하고, value 자리에 바꿔 넣으면 문장이 분명히 틀린 말이 되어야 한다.\n"
    "- explanation: 실제로는 어떻게 정해져 있는지 쉬운 해요체로 한두 문장, 80자 안에 쓴다. 조항 원문에 있는 내용만 쓰고, "
    "조·항 번호나 참·거짓·정답이라는 말은 쓰지 않는다(예: '금연구역에서 담배를 피우면 과태료 10만원을 내야 해요.').\n"
    "- 조항만으로 만들 수 없으면 statement를 빈 문자열로 낸다. 결과는 JSON으로 낸다.")
SCHEMA = {"type": "json_schema", "json_schema": {"name": "quiz_item", "strict": True, "schema": {
    "type": "object", "required": ["statement", "value", "wrong_value", "explanation"], "additionalProperties": False,
    "properties": {"statement": {"type": "string"}, "value": {"type": "string"}, "wrong_value": {"type": "string"},
                   "explanation": {"type": "string"}}}}}
JUDGE = "조항 원문만 근거로 문장이 원문과 맞으면 O, 원문과 다르면 X, 원문으로 가릴 수 없으면 ?를 answer로 낸다. 결과는 JSON으로 낸다."
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
    return [c for c in by_label.values() if MEASURED.search(c["text"])]


def _grounded(item, clause):
    statement, value, wrong = item["statement"], item["value"].strip(), item["wrong_value"].strip()
    known = _numbers(clause["text"])
    written = re.sub(r"\s", "", clause["text"])
    return bool(value) and len(statement) <= MAX_STATEMENT and statement.count(value) == 1 and bool(_numbers(value)) and _numbers(statement) <= known \
        and bool(_numbers(wrong)) and re.sub(r"\s", "", wrong) not in written and _numbers(wrong) != _numbers(value) \
        and _numbers(item["explanation"]) <= known | _numbers(clause["label"])


def _judged(statement, clause):
    import upstage
    raw = upstage.chat([{"role": "system", "content": JUDGE}, {"role": "user", "content": f"[조항 원문]\n{clause['text']}\n\n[문장]\n{statement}"}],
                       response_format=JUDGE_SCHEMA)
    return json.loads(raw)["answer"]


def _item(clause):
    import upstage
    raw = upstage.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"{clause['label']}\n{clause['text'][:1500]}"}],
                       response_format=SCHEMA, temperature=0.3)
    item = json.loads(raw)
    if not _grounded(item, clause):
        return None
    true = item["statement"].strip()
    false = true.replace(item["value"].strip(), item["wrong_value"].strip())
    if _judged(true, clause) != "O" or _judged(false, clause) != "X":
        return None
    return {"label": clause["label"], "law": clause["law"], "source_url": clause["source_url"], "effective_date": clause["effective_date"],
            "text": clause["text"], "true": true, "false": false, "explanation": item["explanation"].strip()}


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
        out.append({key: item[key] for key in ("label", "law", "source_url", "effective_date", "text", "explanation")}
                   | {"question": item[truth], "choices": CHOICES, "answer": 0 if truth == "true" else 1})
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    build()
