# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run from engine: .venv/bin/python eval/validate_questions.py
"""Validate development questions against the local paragraph citation inventory."""

import json
from collections import Counter
from pathlib import Path
from typing import Final

ENGINE: Final = Path(__file__).resolve().parents[1]
TYPE_SHARES: Final = {
    "단순 조회": 0.35,
    "조건 판단": 0.25,
    "여러 조항 결합": 0.20,
    "근거 없음 (함정)": 0.20,
}


def main() -> None:
    """Assert dataset invariants and print counts after all checks pass."""
    questions = json.loads(
        (ENGINE / "eval/dev_questions.json").read_text(encoding="utf-8")
    )
    chunks = json.loads(
        (ENGINE / "data/chunks_paragraph.json").read_text(encoding="utf-8")
    )
    samples = json.loads(
        (ENGINE / "sample_questions.json").read_text(encoding="utf-8")
    )
    labels = {chunk["label"] for chunk in chunks}
    laws = tuple(dict.fromkeys(chunk["law"] for chunk in chunks))
    existing_questions = {item["question"] for item in samples}
    assert isinstance(questions, list), "Expected a question array"
    assert len(questions) >= 60, "At least 60 questions are required"
    ids: set[str] = set()
    seen_questions: set[str] = set()
    type_counts: Counter[str] = Counter()
    law_counts: Counter[str] = Counter()
    citation_count = 0
    gray_count = 0

    for index, item in enumerate(questions, start=1):
        assert isinstance(item, dict), f"Item {index}: expected an object"
        assert {"id", "type", "question", "gold", "answer_key"} <= item.keys()
        assert item.keys() <= {
            "id", "type", "question", "gold", "answer_key", "note"
        }, f"Item {index}: unexpected schema fields"
        identifier = item["id"]
        assert identifier == f"D{index:02d}", f"Nonsequential id: {identifier}"
        assert identifier not in ids, f"Duplicate id: {identifier}"
        ids.add(identifier)
        assert item["type"] in TYPE_SHARES, f"{identifier}: invalid type"
        for field in ("question", "answer_key"):
            assert isinstance(item[field], str) and item[field].strip(), (
                f"{identifier}: missing {field}"
            )
        assert item["question"] not in existing_questions, (
            f"{identifier}: copied sample question"
        )
        assert item["question"] not in seen_questions, (
            f"{identifier}: duplicate question"
        )
        seen_questions.add(item["question"])
        note = item.get("note", "")
        assert isinstance(note, str), f"{identifier}: note must be text"
        gold = item["gold"]
        assert isinstance(gold, list), f"{identifier}: gold must be an array"
        for group in gold:
            assert isinstance(group, list) and group, (
                f"{identifier}: evidence groups must be nonempty arrays"
            )
            for citation in group:
                assert isinstance(citation, str) and citation in labels, (
                    f"{identifier}: unknown citation {citation!r}"
                )
                citation_count += 1
        is_trap = item["type"] == "근거 없음 (함정)"
        assert bool(gold) != is_trap, f"{identifier}: invalid trap evidence"
        if is_trap:
            assert item["answer_key"].startswith("확인 불가"), (
                f"{identifier}: traps must answer 확인 불가"
            )
        if item["type"] == "여러 조항 결합":
            assert len(gold) >= 2, f"{identifier}: multiple evidence required"
        if note.startswith("회색지대"):
            assert is_trap, f"{identifier}: gray zones are traps"
            gray_count += 1
        evidence_text = " ".join(c for group in gold for c in group)
        subject_text = evidence_text if gold else note
        subjects = [law for law in laws if law in subject_text]
        assert len(subjects) == 1, f"{identifier}: ambiguous ordinance subject"
        law_counts[subjects[0]] += 1
        type_counts[item["type"]] += 1

    assert gray_count >= 3, "At least three explained gray zones are required"
    assert set(law_counts) == set(laws), "All six ordinances must be covered"
    for question_type, target in TYPE_SHARES.items():
        actual = type_counts[question_type] / len(questions)
        assert abs(actual - target) <= 0.05, (
            f"{question_type}: {actual:.1%} is outside target tolerance"
        )

    print(
        f"SUCCESS: {len(questions)} questions; {citation_count} gold citations "
        f"match source labels; unique sequential ids; valid types; "
        f"{gray_count} gray-zone traps."
    )
    for law, count in law_counts.items():
        print(f"{law}: {count}")
    for question_type, count in type_counts.items():
        print(f"{question_type}: {count} ({count / len(questions):.0%})")


if __name__ == "__main__":
    main()
