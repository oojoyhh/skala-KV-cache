"""보고서 출력 (Markdown·PDF 렌더링).

- 블록 목록(h1·h2·h3·p·note·table·fixed)을 받아 PDF와 Markdown으로 만든다.
- 한글 폰트는 config.FONT_DIR에서 찾고, 폰트에 없는 글자(그리스 문자·원문자·이모지)는 치환하거나 지운다.
"""

import glob
import os
import re
import unicodedata

from fontTools.ttLib import TTFont
from fpdf import FPDF
from fpdf.fonts import FontFace

import config


def _font_file(bold: bool = False) -> str:
    """FONT_DIR의 한글 폰트. 본문은 이름에 Bold가 없는 파일, 볼드는 *-Bold.ttf (없으면 본문 폰트)."""
    found = sorted(glob.glob(os.path.join(config.FONT_DIR, "*.ttf")))
    regular = [f for f in found if "bold" not in os.path.basename(f).lower()]
    if not regular:
        raise FileNotFoundError(f"한글 폰트가 없습니다. {config.FONT_DIR}/ 에 .ttf 파일을 두세요.")
    bolds = [f for f in found if os.path.basename(f).lower().endswith("-bold.ttf")]
    return (bolds or regular)[0] if bold else regular[0]


BOLD = FontFace(emphasis="BOLD")


LABELS = ("충분성 검사 미달 사유", "데이터 수집 오류", "판정 결과")   # 코드가 붙이는 6장 소제목 줄


def _printable(text: str, cmap: dict) -> str:
    """폰트에 없는 글자가 PDF에서 조용히 사라지지 않게 바꾼다: α → alpha, ① → (1), 그 밖(이모지 등)은 제거."""
    out = []
    for ch in text:
        if ord(ch) in cmap or ch == "\n":
            out.append(ch)
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("GREEK"):
            out.append(name.split()[-1].lower().replace("lamda", "lambda"))  # 유니코드 이름 표기가 LAMDA
        elif name.startswith(("CIRCLED DIGIT", "CIRCLED NUMBER")):
            out.append(f"({unicodedata.numeric(ch):g})")
    return "".join(out)


def render_pdf(blocks, path: str) -> None:
    font = _font_file()
    cmap = TTFont(font).getBestCmap()
    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_font("ko", fname=font)
    pdf.add_font("ko", style="B", fname=_font_file(bold=True))
    pdf.add_page()
    sizes = {"h1": 18, "h2": 14, "h3": 12}
    for kind, content in blocks:
        content = [[_printable(c, cmap) for c in row] for row in content] if kind == "table" else _printable(content, cmap)
        if kind in sizes:
            pdf.ln(4)
            pdf.set_font("ko", style="B", size=sizes[kind])
            pdf.multi_cell(0, 8, content, align="L", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif kind == "note":
            pdf.set_font("ko", size=8)
            pdf.multi_cell(0, 5, content, align="L", new_x="LMARGIN", new_y="NEXT")
        elif kind == "table":
            pdf.set_font("ko", size=8)
            with pdf.table(text_align="LEFT", line_height=5, headings_style=BOLD) as table:
                for row in content:
                    cells = table.row()
                    for i, cell in enumerate(row):
                        cells.cell(cell, style=BOLD if i == 0 else None)   # 머리행 + 첫 열(기술·지표명) 볼드
            pdf.ln(2)
        else:
            pdf.set_font("ko", size=10)
            for line in content.replace("**", "").splitlines():
                line = line.lstrip("#").lstrip() if line.startswith("#") else line  # LLM이 붙인 마크다운 제목 기호 제거
                pdf.set_font("ko", style="B" if line.startswith(LABELS) else "", size=10)
                pdf.multi_cell(0, 6, line.rstrip(), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)


def to_markdown(blocks) -> str:
    """디버깅·README 인용용 Markdown 사본."""
    out = []
    for kind, content in blocks:
        if kind == "table":
            out += ["| " + " | ".join(content[0]) + " |", "|" + "---|" * len(content[0])]
            out += ["| " + " | ".join(r) + " |" for r in content[1:]]
        else:
            out.append({"h1": "# ", "h2": "## ", "h3": "### "}.get(kind, "") + content)
        out.append("")
    return "\n".join(out)
