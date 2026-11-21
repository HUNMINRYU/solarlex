import functools
import json
import sqlite3

import config

TARGET_PREFIX = "대상:"
RELATIONS = ("BENEFITS", "OBLIGES")
USAGE_DB = config.DATA / "usage.db"
AGE_BANDS = ("10대", "20대", "30대", "40대 이상")
MIN_ANSWERS_FOR_STATS = 20
MAX_FOLLOW_UPS = 3
MAX_RECOMMENDED = 6
QUESTION_SCHEMA = {"type": "json_schema", "json_schema": {"name": "recommended_questions", "strict": True, "schema": {
    "type": "object", "properties": {"questions": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_RECOMMENDED}},
    "required": ["questions"], "additionalProperties": False}}}


@functools.cache
def _ordinances():
    laws = json.loads((config.DATA / "ordinances.json").read_text(encoding="utf-8"))
    return {law["name"]: {"source_url": law["source_url"], "effective_date": law["effective_date"],
                          "department": law["department"], "phone": law["phone"]} for law in laws}


@functools.cache
def _clauses_by_target():
    graph = sqlite3.connect(config.DATA / "solarlex.db")
    rows = graph.execute(
        "SELECT e.dst, e.rel, e.src, n.law, n.title, e.note FROM edges e JOIN nodes n ON n.id = e.src "
        f"WHERE e.rel IN ({','.join('?' * len(RELATIONS))}) ORDER BY e.dst, e.rel, e.src", RELATIONS).fetchall()
    graph.close()
    clauses = {}
    for target, relation, article, law, title, note in rows:
        kind, _, detail = note.partition(": ")
        name = target.removeprefix(TARGET_PREFIX)
        clauses.setdefault(name, []).append({
            "relation": relation, "kind": kind, "detail": detail, "law": law,
            "article": article.removeprefix(law).strip(), "title": title,
            "question": f"{article}({title})에서 {name}에게 해당하는 내용을 알려 주세요.", **_ordinances()[law]})
    return clauses


def targets():
    return [{"name": name, "clauses": len(clauses)} for name, clauses in _clauses_by_target().items()]


def clauses_for(names):
    known = _clauses_by_target()
    merged = {}
    for name in dict.fromkeys(names):
        for clause in known.get(name, []):
            entry = merged.setdefault((clause["relation"], clause["law"], clause["article"]), {**clause, "targets": []})
            entry["targets"].append(name)
    return sorted(merged.values(), key=lambda clause: clause["law"])


@functools.cache
def _shortest_question_by_clause():
    graph = sqlite3.connect(config.DATA / "solarlex.db")
    rows = graph.execute("SELECT label, question FROM questions ORDER BY length(question) DESC").fetchall()
    graph.close()
    return dict(rows)


def follow_ups(citation, asked):
    known = _shortest_question_by_clause()
    questions = dict.fromkeys(known[label] for label in citation if label in known)
    return [question for question in questions if question != asked][:MAX_FOLLOW_UPS]


def _usage():
    usage = sqlite3.connect(USAGE_DB, timeout=5)
    usage.execute("CREATE TABLE IF NOT EXISTS answers (age_band TEXT, law TEXT, answers INTEGER, PRIMARY KEY (age_band, law))")
    return usage


def record_answer(age_band, citation):
    if age_band not in AGE_BANDS:
        return
    laws = {law for label in citation for law in _ordinances() if label.startswith(law)}
    with _usage() as usage:
        usage.executemany("INSERT INTO answers VALUES (?, ?, 1) ON CONFLICT (age_band, law) DO UPDATE SET answers = answers + 1",
                          [(age_band, law) for law in laws])


def answers_by_ordinance(age_band):
    with _usage() as usage:
        rows = usage.execute("SELECT law, answers FROM answers WHERE age_band = ? ORDER BY answers DESC", (age_band,)).fetchall()
    total = sum(answers for _, answers in rows)
    enough = total >= MIN_ANSWERS_FOR_STATS
    return {"age_band": age_band, "total": total, "minimum": MIN_ANSWERS_FOR_STATS,
            "by_ordinance": [{"law": law, "answers": answers} for law, answers in rows] if enough else []}


@functools.cache
def _recommended(names):
    import upstage
    listed = "\n".join(f"- [{', '.join(c['targets'])}] ({c['kind']}) {c['law']} {c['article']} {c['title']}: {c['detail']}"
                       for c in clauses_for(names))
    raw = upstage.chat([
        {"role": "system", "content": (
            "너는 서울시 조례를 다 아는 안내원으로, 시민에게 물어보면 좋을 질문을 추천한다. "
            "한 시민이 고른 자기 상황들과, 그 상황에 해당하는 조례 조항이 주어진다. 각 조항 앞 대괄호는 그 조항이 걸리는 상황이다. "
            f"고른 상황을 모두 가진 한 사람의 처지에서 생활에 가장 도움이 될 질문을 {MAX_RECOMMENDED}개까지 추천한다. "
            "고른 상황이 여럿이면 상황마다 적어도 하나씩 다루고, 여러 상황이 함께 걸리는 조항이 있으면 그 조항을 먼저 다룬다. "
            "질문은 시민이 자기 처지에서 말하듯 일상어 한 문장으로 짧게 쓰고, 조항 번호나 조례 이름, 조문 투의 표현은 넣지 않는다. "
            "조항에 적힌 답(금액, 비율, 기준, 방법)을 질문에 미리 넣어 맞는지 확인하는 식으로 묻지 않고, 무엇을·얼마나·어떻게 받거나 지켜야 하는지를 묻는다. "
            "조항에 적혀 있지 않은 것(신청 방법, 일정, 장소 등)은 묻지 않는다. 주어진 조항만으로 답할 수 있어야 한다. 결과는 JSON으로 낸다.")},
        {"role": "user", "content": f"[고른 상황] {', '.join(names)}\n\n[해당 조항]\n{listed}"}],
        response_format=QUESTION_SCHEMA)
    return [question for question in json.loads(raw)["questions"] if question.strip()]


def recommended_questions(names):
    known = _clauses_by_target()
    chosen = tuple(sorted(name for name in set(names) if name in known))
    return _recommended(chosen) if chosen else []
