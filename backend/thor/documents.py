"""Short documents Thor writes itself, such as the revision plan and the reply letter, in Markdown, PDF and Word.

A document is a list of blocks: (kind, text), where kind is title, heading, meta, text, quote or item. The text is
what a person wrote or a model answered, not LaTeX, so it is escaped for the PDF.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from .paths import run_tool, which

Block = tuple[str, str]
PDF_SECONDS = 120
# Times New Roman comes with macOS and has the Greek and mathematical signs a scientist's text is full of.
PREAMBLE = r"""\documentclass[11pt]{article}
\usepackage[margin=2.5cm]{geometry}
\usepackage{fontspec}
\IfFontExistsTF{Times New Roman}{\setmainfont{Times New Roman}}{}
\usepackage{parskip}
\usepackage{xcolor}
\pagestyle{plain}
\begin{document}
"""
LATEX_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
                 "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
SPECIAL = re.compile("|".join(re.escape(c) for c in LATEX_SPECIAL))
# Characters XML 1.0 does not allow, which would make Word refuse the whole file.
NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


class DocumentError(Exception):
    """Told to the user as it is."""


def latex_escape(text: str) -> str:
    return SPECIAL.sub(lambda m: LATEX_SPECIAL[m.group(0)], text)


def to_markdown(blocks: list[Block]) -> str:
    out = []
    for kind, text in blocks:
        if kind == "title":
            out.append(f"# {text}")
        elif kind == "heading":
            out.append(f"## {text}")
        elif kind == "quote":
            out.append("\n".join(f"> {line}" for line in text.splitlines()))
        elif kind == "item":
            out.append(f"- {text}")
        elif kind == "meta":
            out.append(f"*{text}*")
        else:
            out.append(text)
    return "\n\n".join(out) + "\n"


def to_latex(blocks: list[Block]) -> str:
    out = [PREAMBLE]
    in_list = False
    for kind, text in blocks:
        if in_list and kind != "item":
            out.append(r"\end{itemize}")
            in_list = False
        body = latex_escape(text).replace("\n\n", "\n\n\\par ").replace("\n", " ")
        if kind == "title":
            out.append(rf"{{\LARGE\bfseries {body}\par}}\vspace{{0.5em}}")
        elif kind == "heading":
            out.append(rf"\vspace{{0.6em}}{{\large\bfseries {body}\par}}\nopagebreak")
        elif kind == "meta":
            out.append(rf"{{\small\color{{black!65}} {body}\par}}")
        elif kind == "quote":
            out.append(rf"\begin{{quote}}\small\itshape {body}\end{{quote}}")
        elif kind == "item":
            if not in_list:
                out.append(r"\begin{itemize}")
                in_list = True
            out.append(rf"\item {body}")
        else:
            out.append(f"{body}\n")
    if in_list:
        out.append(r"\end{itemize}")
    out.append(r"\end{document}")
    return "\n".join(out) + "\n"


def to_pdf(blocks: list[Block], target: Path) -> None:
    engine = which("xelatex") or which("lualatex")
    if not engine:
        raise DocumentError("Making this PDF needs XeLaTeX or LuaLaTeX, which come with MacTeX. The Markdown and Word "
                            "files do not need them.")
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "document.tex"
        source.write_text(to_latex(blocks), encoding="utf-8")
        code, output = run_tool([engine, "-interaction=nonstopmode", "-halt-on-error", source.name], timeout=PDF_SECONDS,
                                cwd=folder)
        built = source.with_suffix(".pdf")
        if code != 0 or not built.is_file():
            errors = [line for line in output.splitlines() if line.startswith("!")]
            raise DocumentError(f"LaTeX could not make the PDF: {errors[0] if errors else output[-200:]}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(built.read_bytes())


def _run(text: str, properties: str = "") -> str:
    clean = escape(NOT_XML.sub("", text))
    return f'<w:r>{f"<w:rPr>{properties}</w:rPr>" if properties else ""}<w:t xml:space="preserve">{clean}</w:t></w:r>'


def _paragraph(text: str, style: str = "", properties: str = "") -> str:
    head = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{head}{_run(text, properties)}</w:p>"


THUMBNAIL_WIDTH = 900


def thumbnail(path: Path) -> bytes:
    """A figure as a PNG the editor can show: browsers cannot show a PDF or EPS inline."""
    import pymupdf

    try:
        with pymupdf.open(path) as doc:
            page = doc[0]
            scale = min(THUMBNAIL_WIDTH / max(page.rect.width, 1), 3)
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
    except (RuntimeError, ValueError) as e:
        raise DocumentError(f"{path.name} could not be drawn: {e}") from e


def to_docx(blocks: list[Block], target: Path) -> None:
    """A Word file, written directly: its format is a zip of a few XML files, and this needs nothing more than that."""
    body = []
    for kind, text in blocks:
        for piece in text.split("\n\n") if kind == "text" else [text]:
            piece = piece.replace("\n", " ")
            if kind == "title":
                body.append(_paragraph(piece, "Title"))
            elif kind == "heading":
                body.append(_paragraph(piece, "Heading1"))
            elif kind == "quote":
                body.append(_paragraph(piece, "Quote"))
            elif kind == "item":
                body.append(_paragraph(piece, "ListParagraph"))
            elif kind == "meta":
                body.append(_paragraph(piece, "", "<w:i/>"))
            else:
                body.append(_paragraph(piece))
    namespace = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {namespace}><w:body>'
                f'{"".join(body)}<w:sectPr/></w:body></w:document>')

    def style(ident: str, name: str, extra: str) -> str:
        return f'<w:style w:type="paragraph" w:styleId="{ident}"><w:name w:val="{name}"/>{extra}</w:style>'

    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {namespace}>'
              + style("Title", "Title", '<w:rPr><w:b/><w:sz w:val="36"/></w:rPr>')
              + style("Heading1", "heading 1", '<w:pPr><w:spacing w:before="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr>')
              + style("Quote", "Quote", '<w:pPr><w:ind w:left="480"/></w:pPr><w:rPr><w:i/></w:rPr>')
              + style("ListParagraph", "List Paragraph", '<w:pPr><w:ind w:left="360"/></w:pPr>')
              + "</w:styles>")
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                     'wordprocessingml.document.main+xml"/>'
                     '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                     'wordprocessingml.styles+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'officeDocument" Target="word/document.xml"/></Relationships>')
    word_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
                 'Target="styles.xml"/></Relationships>')
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", word_rels)
