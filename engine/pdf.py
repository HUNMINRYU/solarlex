import functools
import re
from datetime import date
from html import escape

import config
import kb

STYLE = config.ROOT / "web" / "pdf.css"
FONTS = config.ROOT / "web" / "fonts" / "pdf"
FONT_FILES = {400: "Pretendard-Regular.woff2", 700: "Pretendard-Bold.woff2", 900: "Pretendard-Black.woff2"}
ORDINANCE_COLOR = {"주차장": "#3d5bd9", "동물보호": "#b5651d", "자전거": "#2f8a4f", "도시공원": "#0f7f8a", "청년": "#7a4fd1", "금연": "#c0394b"}
DEFAULT_COLOR = "#6d7882"
CLAUSE_HEADING = re.compile(r"^\[[^\]]*\]\n")
TABLE_ROW_END = re.compile(r"</tr>")
TABLE_CELL_END = re.compile(r"</t[dh]>")
TABLE_TAG = re.compile(r"</?(?:table|tr|td|th|tbody|thead|br)[^>]*>")
CONCLUSION = re.compile(r"^(.+?[.다요])\s+(.*)$", re.S)
DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
ARTICLE = re.compile(r"(제\d+조(?:의\d+)?(?:\s?제\d+항)?|별표\s?\d*|별도\s?\d*)$")


@functools.cache
def _fonts():
    from weasyprint import CSS
    from weasyprint.text.fonts import FontConfiguration
    fonts = FontConfiguration()
    faces = "".join(f'@font-face {{ font-family: "Pretendard"; font-weight: {weight}; src: url("{(FONTS / file).as_uri()}"); }}'
                    for weight, file in FONT_FILES.items())
    return fonts, [CSS(string=faces + STYLE.read_text(encoding="utf-8"), font_config=fonts)]


def _ordinance(label):
    return next((name for name in ORDINANCE_COLOR if name in label), "")


def _short(label):
    name, article = _ordinance(label), ARTICLE.search(label)
    return f"{name} 조례 {article.group(1)}" if name and article else label.replace("서울특별시 ", "")


def _clause_text(text):
    text = TABLE_CELL_END.sub("  ", TABLE_ROW_END.sub("\n", kb.AMEND_NOTE.sub("", CLAUSE_HEADING.sub("", text))))
    return TABLE_TAG.sub("", text).strip()


def _document(question, situations, answer, evidence):
    conclusion, detail = (CONCLUSION.match(answer).groups() if CONCLUSION.match(answer) else (answer, ""))
    today = date.today()
    marks = "".join(f'<i style="background:{color}"></i>' for color in ORDINANCE_COLOR.values())
    chosen = "".join(f"<span>{escape(name)}</span>" for name in situations)
    refs = "".join(f'<li><span class="num" style="--c:{ORDINANCE_COLOR.get(_ordinance(ev["label"]), DEFAULT_COLOR)}">{i}</span>'
                   f'{escape(ARTICLE.search(ev["label"]).group(1) if ARTICLE.search(ev["label"]) else ev["label"])}</li>'
                   for i, ev in enumerate(evidence, 1))
    departments = dict.fromkeys((ev["department"], ev["phone"]) for ev in evidence if ev.get("department"))
    contact = " · ".join(f"서울시 {escape(department)} <b>{escape(phone)}</b>" for department, phone in departments)
    sources = "".join(
        f'<section class="source" style="--c:{ORDINANCE_COLOR.get(_ordinance(ev["label"]), DEFAULT_COLOR)}">'
        f'<header><span class="num">{i}</span><h3>{escape(_short(ev["label"]))}</h3>'
        f'<span class="meta">시행 {escape(DATE.sub(lambda m: f"{m[1]}. {int(m[2])}. {int(m[3])}.", ev["effective_date"]))}</span></header>'
        f'<p class="clause">{escape(_clause_text(ev["text"]))}</p>'
        f'<p class="link">원문 {escape(ev["source_url"])}</p></section>'
        for i, ev in enumerate(evidence, 1))
    return f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>SolarLex 답변</title><body>
<div class="top"><div class="brand"><span class="mark">{marks}</span>SolarLex</div>
<p class="made">서울특별시 조례 안내<br>만든 날 {today.year}. {today.month}. {today.day}.</p></div>
{f'<p class="situations">고른 상황 {chosen}</p>' if chosen else ''}
<p class="question">{escape(question)}</p>
<div class="answer"><p class="conclusion">{escape(conclusion)}</p>
{f'<p class="detail">{escape(detail)}</p>' if detail else ''}
<ul class="refs">{refs}</ul>
{f'<p class="contact">이 조례의 담당 부서: {contact}</p>' if contact else ''}</div>
<h2>근거 원문 {len(evidence)}개</h2>{sources}
</body></html>"""


def answer_pdf(question, situations, answer, evidence):
    from weasyprint import HTML
    fonts, styles = _fonts()
    return HTML(string=_document(question, situations, answer, evidence)).write_pdf(stylesheets=styles, font_config=fonts)
