"""2단계(대안): 별표 원본 HWP를 rhwp로 읽어 표 구조를 살린 Markdown으로 저장합니다.

parse_annex.py(Upstage Document Parse)와 같은 경로(data/annex/<mst>_<라벨>.md)에 저장하므로
build_index.py가 그대로 읽습니다. API 키 없이 동작합니다.

실행:  python -X utf8 parse_annex_rhwp.py [--force]
"""
import json
import sys

import requests
import rhwp
from rhwp.ir.nodes import TableBlock

import config
from parse_annex import annex_path


def hwp_to_markdown(path):
    ir = rhwp.parse(str(path)).to_ir()
    md = ir.to_markdown()
    nested = [b.html for b in ir.iter_blocks(scope="body")
              if isinstance(b, TableBlock) and b.rows > 1 and b.html not in md]
    return "\n\n".join([md, *nested])


def main(force=False):
    laws = json.loads((config.DATA / "ordinances.json").read_text(encoding="utf-8"))
    files = config.DATA / "annex_files"
    files.mkdir(parents=True, exist_ok=True)
    (config.DATA / "annex").mkdir(parents=True, exist_ok=True)

    for law in laws:
        for ax in law["annexes"]:
            if not ax["label"].startswith("별표") or not ax["file_url"]:
                continue
            out = annex_path(law["mst"], ax["label"])
            if out.exists() and not force:
                print("· 이미 있음:", out.name)
                continue
            src = files / f"{law['mst']}_{ax['label'].replace(' ', '')}.{ax['file_type'] or 'hwp'}"
            if not src.exists():
                r = requests.get(ax["file_url"], timeout=60)
                r.raise_for_status()
                src.write_bytes(r.content)
            md = hwp_to_markdown(src)
            out.write_text(f"# {law['name']} {ax['label']} {ax['title']}\n\n{md}", encoding="utf-8")
            print(f"✓ {law['name']} {ax['label']}  ({len(md)}자)")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
