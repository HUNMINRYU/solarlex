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
MAP_WIDTH, MAP_CITED, MAP_HUB, MAP_RELATED = 700, 215, 370, 500
MAP_TOP, MAP_ROW, MAP_GROUP_GAP, MAP_TITLE_CHARS = 40, 24, 10, 15
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


def _color(law):
    return ORDINANCE_COLOR.get(_ordinance(law), DEFAULT_COLOR)


def _article(label):
    found = ARTICLE.search(label)
    return found.group(1) if found else label


def _fit(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _curve(x1, y1, x2, y2, color, width):
    middle = (x1 + x2) / 2
    return (f'<path d="M{x1} {y1} C{middle} {y1} {middle} {y2} {x2} {y2}" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-opacity="0.75" stroke-linecap="round"/>')


def _map(chart):
    related_y, marks, y = {}, [], MAP_TOP
    for group in chart["groups"]:
        name = f'{_ordinance(group["law"])} 조례'
        marks.append(f'<rect x="{MAP_RELATED - 8}" y="{y - 11}" width="{len(name) * 9.5 + 14}" height="16" rx="8" fill="{_color(group["law"])}"/>'
                     f'<text x="{MAP_RELATED - 1}" y="{y + 1}" font-size="9" font-weight="700" fill="#fff">{escape(name)}</text>')
        y += MAP_ROW
        for item in group["items"]:
            related_y[item["id"]] = y
            marks.append(f'<circle cx="{MAP_RELATED}" cy="{y}" r="4.5" fill="#fff" stroke="{_color(group["law"])}" stroke-width="2.2"/>'
                         f'<text x="{MAP_RELATED + 11}" y="{y + 3.5}" font-size="9.5"><tspan font-weight="700">{escape(_article(item["id"]))}</tspan> '
                         f'{escape(_fit(item["title"], MAP_TITLE_CHARS))}</text>')
            y += MAP_ROW
        y += MAP_GROUP_GAP
    bottom = max(y - MAP_GROUP_GAP - MAP_ROW, MAP_TOP + MAP_ROW * max(len(chart["hubs"]), len(chart["cited"])))
    spread = lambda count, i: MAP_TOP + (bottom - MAP_TOP) * (i + 0.5) / count
    hub_y = {hub["id"]: spread(len(chart["hubs"]), i) for i, hub in enumerate(chart["hubs"])}
    cited_y = {cited["id"]: spread(len(chart["cited"]), i) for i, cited in enumerate(chart["cited"])}
    law_of = {item["id"]: group["law"] for group in chart["groups"] for item in group["items"]} | {c["id"]: c["law"] for c in chart["cited"]}
    thin = {hub["id"] for hub in chart["hubs"] if hub["thin"]}
    lines = []
    for link in chart["links"]:
        width = 1.3 if link["hub"] in thin else 2.4
        if link["art"] in cited_y:
            lines.append(_curve(MAP_CITED, cited_y[link["art"]], MAP_HUB, hub_y[link["hub"]], _color(law_of[link["art"]]), width))
        else:
            lines.append(_curve(MAP_HUB, hub_y[link["hub"]], MAP_RELATED, related_y[link["art"]], _color(law_of[link["art"]]), width))
    for link in chart["direct"]:
        lines.append(_curve(MAP_CITED, cited_y[link["cited"]], MAP_RELATED, related_y[link["rel"]], _color(law_of[link["rel"]]), 1.3))
    for hub in chart["hubs"]:
        width = len(hub["label"]) * 9.5 + 18
        marks.append(f'<rect x="{MAP_HUB - width / 2}" y="{hub_y[hub["id"]] - 10}" width="{width}" height="20" rx="10" fill="#fff" stroke="#1e2124" stroke-width="1.6"/>'
                     f'<text x="{MAP_HUB}" y="{hub_y[hub["id"]] + 3.5}" font-size="9.5" font-weight="700" text-anchor="middle">{escape(hub["label"])}</text>'
                     f'<text x="{MAP_HUB}" y="{hub_y[hub["id"]] - 14}" font-size="7.5" fill="#6d7882" text-anchor="middle">{escape(hub["kind"])}</text>')
    for cited in chart["cited"]:
        cy = cited_y[cited["id"]]
        marks.append(f'<circle cx="{MAP_CITED}" cy="{cy}" r="6" fill="{_color(cited["law"])}"/>'
                     f'<text x="{MAP_CITED - 12}" y="{cy - 1}" font-size="10" font-weight="700" fill="#0b50d0" text-anchor="end">{escape(_short(cited["id"]))}</text>'
                     f'<text x="{MAP_CITED - 12}" y="{cy + 11}" font-size="8.5" fill="#464c53" text-anchor="end">{escape(_fit(cited["title"], MAP_TITLE_CHARS))}</text>')
    heads = "".join(f'<text x="{x}" y="14" font-size="8" fill="#6d7882" text-anchor="{anchor}">{name}</text>'
                    for x, anchor, name in ((MAP_CITED, "end", "답변 근거"), (MAP_HUB, "middle", "연결 고리"), (MAP_RELATED, "start", "다른 조례 조항")))
    return (f'<svg class="map" viewBox="0 0 {MAP_WIDTH} {bottom + MAP_ROW}" font-family="Pretendard" fill="#1e2124">'
            f'{heads}{"".join(lines)}{"".join(marks)}</svg>')


def _related(related):
    groups = {}
    for item in related:
        groups.setdefault(item["law"], []).append(item)
    return "".join(
        f'<h3 class="law" style="--c:{_color(law)}">{escape(_ordinance(law))} 조례 {len(items)}개</h3>' + "".join(
            f'<section class="source" style="--c:{_color(law)}"><header><h3>{escape(_article(item["label"]))} {escape(item["title"])}</h3></header>'
            + (f'<p class="why">연결 이유: {escape(" / ".join(item["reasons"]))}</p>' if item["reasons"] else "")
            + f'<p class="clause">{escape(_clause_text(item["text"]))}</p><p class="link">원문 {escape(item["source_url"])}</p></section>'
            for item in items)
        for law, items in groups.items())


def _extras(extras):
    return '<ul class="extras">' + "".join(
        f'<li><span class="kind">{escape(extra["kind"])}</span><div><b>{escape(extra["name"])}</b>'
        + (f'<p class="why">원문 일부: 「…{escape(extra["reason"])}…」</p>' if extra["reason"] else "") + "</div></li>"
        for extra in extras) + "</ul>"


def _document(question, situations, answer, evidence, related, chart, extras):
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
{f'<h2>다른 조례 조항 {len(related)}개</h2><p class="note">답변 근거는 아니지만 같은 대상이나 주제로 이어진 다른 조례 조항입니다.</p>{_related(related)}' if related else ''}
{f'<div class="keep"><h2>연관 조례 지도</h2><p class="note">답변 근거에서 같은 대상과 주제를 따라 다른 조례 조항으로 이어지는 길입니다. 선 색은 조례를 뜻하고, 굵은 선은 같은 대상·용어, 가는 선은 같은 주제입니다.</p>{_map(chart)}</div>' if chart and chart["groups"] else ''}
{f'<h2>관련 법령·조항 {len(extras)}개</h2><p class="note">답변 근거 조항이 근거로 삼거나 가리키는 법령과 같은 조례의 다른 조항입니다.</p>{_extras(extras)}' if extras else ''}
</body></html>"""


def answer_pdf(question, situations, answer, evidence, related, chart, extras):
    from weasyprint import HTML
    fonts, styles = _fonts()
    return HTML(string=_document(question, situations, answer, evidence, related, chart, extras)).write_pdf(stylesheets=styles, font_config=fonts)
