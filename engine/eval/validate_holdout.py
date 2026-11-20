# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run from engine: .venv/bin/python eval/validate_holdout.py
"""Validate held-out questions against the local paragraph citation inventory.

Adapted from validate_questions.py for 40 H-series questions, equal type
counts, development/sample exclusion, and two answerable trap lookalikes.
Citation membership is checked mechanically; legal entailment and topic
novelty require reading the source text when authoring the questions.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Final

ENGINE: Final = Path(__file__).resolve().parents[1]
TYPE_COUNTS: Final = {
    "단순 조회": 10,
    "조건 판단": 10,
    "여러 조항 결합": 10,
    "근거 없음 (함정)": 10,
}
ANSWERABLE_NOTE: Final = "함정처럼 보이지만 답 있음"


def main() -> None:
    """Assert holdout invariants and print counts only after all checks pass."""
    questions = json.loads(
        (ENGINE / "eval/holdout_questions.json").read_text(encoding="utf-8")
    )
    chunks = json.loads(
        (ENGINE / "data/chunks_paragraph.json").read_text(encoding="utf-8")
    )
    samples = json.loads(
        (ENGINE / "sample_questions.json").read_text(encoding="utf-8")
    )
    development = json.loads(
        (ENGINE / "eval/dev_questions.json").read_text(encoding="utf-8")
    )
    labels = {chunk["label"] for chunk in chunks}
    laws = tuple(dict.fromkeys(chunk["law"] for chunk in chunks))
    existing_questions = {
        item["question"] for item in [*samples, *development]
    }
    assert isinstance(questions, list), "Expected a question array"
    assert len(questions) == 40, "Exactly 40 questions are required"
    ids: set[str] = set()
    seen_questions: set[str] = set()
    type_counts: Counter[str] = Counter()
    law_counts: Counter[str] = Counter()
    citation_count = 0
    answerable_count = 0

    for index, item in enumerate(questions, start=1):
        assert isinstance(item, dict), f"Item {index}: expected an object"
        assert {"id", "type", "question", "gold", "answer_key"} <= item.keys()
        assert item.keys() <= {
            "id", "type", "question", "gold", "answer_key", "note"
        }, f"Item {index}: unexpected schema fields"
        identifier = item["id"]
        assert identifier == f"H{index:02d}", f"Nonsequential id: {identifier}"
        assert identifier not in ids, f"Duplicate id: {identifier}"
        ids.add(identifier)
        assert item["type"] in TYPE_COUNTS, f"{identifier}: invalid type"
        for field in ("question", "answer_key"):
            assert isinstance(item[field], str) and item[field].strip(), (
                f"{identifier}: missing {field}"
            )
        assert item["question"] not in existing_questions, (
            f"{identifier}: copied development or sample question"
        )
        assert item["question"] not in seen_questions, (
            f"{identifier}: duplicate question"
        )
        seen_questions.add(item["question"])
        note = item.get("note", "")
        assert isinstance(note, str), f"{identifier}: note must be text"
        gold = item["gold"]
        assert isinstance(gold, list), f"{identifier}: gold must be an array"
        used_labels: set[str] = set()
        for group in gold:
            assert isinstance(group, list) and group, (
                f"{identifier}: evidence groups must be nonempty arrays"
            )
            for citation in group:
                assert isinstance(citation, str) and citation in labels, (
                    f"{identifier}: unknown citation {citation!r}"
                )
                assert citation not in used_labels, (
                    f"{identifier}: repeated evidence label {citation!r}"
                )
                used_labels.add(citation)
                citation_count += 1
        is_trap = item["type"] == "근거 없음 (함정)"
        assert bool(gold) != is_trap, f"{identifier}: invalid trap evidence"
        if is_trap:
            assert item["answer_key"].startswith("확인 불가"), (
                f"{identifier}: traps must answer 확인 불가"
            )
        needs_combination = item["type"] == "여러 조항 결합"
        if needs_combination:
            assert len(gold) >= 2, f"{identifier}: multiple evidence required"
        is_answerable_lookalike = note == ANSWERABLE_NOTE
        if is_answerable_lookalike:
            assert bool(gold) and not is_trap, (
                f"{identifier}: trap lookalikes must be answerable"
            )
            answerable_count += 1
        evidence_text = " ".join(c for group in gold for c in group)
        subject_text = evidence_text if gold else note
        subjects = [law for law in laws if law in subject_text]
        assert len(subjects) == 1, f"{identifier}: ambiguous ordinance subject"
        law_counts[subjects[0]] += 1
        type_counts[item["type"]] += 1

    assert answerable_count == 2, "Exactly two answerable lookalikes required"
    assert len(laws) == 6, "Expected six source ordinances"
    assert set(law_counts) == set(laws), "All six ordinances must be covered"
    assert dict(type_counts) == TYPE_COUNTS, (
        f"Expected ten questions per type, got {dict(type_counts)}"
    )
    print(
        f"SUCCESS: {len(questions)} questions; {citation_count} gold citations "
        f"match source labels; unique sequential ids H01..H40; valid types; "
        f"{answerable_count} answerable trap lookalikes; "
        "no exact development/sample duplicates."
    )
    for law in laws:
        print(f"{law}: {law_counts[law]}")
    for question_type, count in type_counts.items():
        print(f"{question_type}: {count} ({count / len(questions):.0%})")


if __name__ == "__main__":
    main()
