from __future__ import annotations

import hashlib
import html
import os
import re
import tempfile
from pathlib import Path

_matplotlib_cache = Path(tempfile.gettempdir()) / "homework-agent-matplotlib"
_matplotlib_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_matplotlib_cache))

from matplotlib import mathtext
from PIL import Image as PillowImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


INLINE_MATH_RE = re.compile(r"\\\((.+?)\\\)|(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)")
BLOCK_MATH_RE = re.compile(r"^\s*(?:\\\[(.+?)\\\]|\$\$(.+?)\$\$)\s*$")


def _font_path(bold: bool = False) -> Path:
    candidates = (
        [Path(r"C:\Windows\Fonts\arialbd.ttf")]
        if bold
        else [Path(r"C:\Windows\Fonts\arial.ttf")]
    )
    candidates += [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError("Не найден шрифт с поддержкой кириллицы (Arial/DejaVu Sans).")


class HomeworkPdfService:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir.resolve()
        self.formula_dir = self.output_dir / ".formula-cache"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.formula_dir.mkdir(parents=True, exist_ok=True)
        self._register_fonts()

    @staticmethod
    def _register_fonts() -> None:
        if "HomeworkRegular" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("HomeworkRegular", str(_font_path())))
        if "HomeworkBold" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("HomeworkBold", str(_font_path(bold=True))))

    def _formula_image(self, latex: str) -> tuple[Path, float, float]:
        normalized = latex.strip().replace("\\dfrac", "\\frac")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        path = self.formula_dir / f"{digest}.png"
        if not path.exists():
            mathtext.math_to_image(
                f"${normalized}$",
                str(path),
                dpi=240,
                format="png",
                color="#172033",
            )
        with PillowImage.open(path) as image:
            pixel_width, pixel_height = image.size
        height = 15.5
        width = min(460.0, height * pixel_width / max(pixel_height, 1))
        return path, width, height

    def _inline_markup(self, text: str) -> str:
        text = text.replace("\\\\(", "\\(").replace("\\\\)", "\\)")
        pieces: list[str] = []
        cursor = 0
        for match in INLINE_MATH_RE.finditer(text):
            pieces.append(html.escape(text[cursor : match.start()]))
            latex = match.group(1) or match.group(2) or ""
            try:
                image_path, width, height = self._formula_image(latex)
                source = html.escape(image_path.as_posix(), quote=True)
                pieces.append(
                    f'<img src="{source}" width="{width:.1f}" height="{height:.1f}" valign="-4"/>'
                )
            except Exception:
                pieces.append(html.escape(latex))
            cursor = match.end()
        pieces.append(html.escape(text[cursor:]))
        return "".join(pieces).replace("\n", "<br/>")

    def _page(self, canvas, document) -> None:
        canvas.saveState()
        width, _ = A4
        canvas.setStrokeColor(colors.HexColor("#D9E2F1"))
        canvas.line(20 * mm, 16 * mm, width - 20 * mm, 16 * mm)
        canvas.setFont("HomeworkRegular", 8.5)
        canvas.setFillColor(colors.HexColor("#718096"))
        canvas.drawString(20 * mm, 10.5 * mm, "Homework Agent")
        canvas.drawRightString(width - 20 * mm, 10.5 * mm, f"Страница {document.page}")
        canvas.restoreState()

    def render(
        self,
        homework_id: int,
        student: str,
        text: str,
        deadline: str | None = None,
        output_path: Path | None = None,
    ) -> Path:
        path = (output_path or self.output_dir / f"homework_{homework_id}.pdf").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "HomeworkTitle",
            parent=styles["Title"],
            fontName="HomeworkBold",
            fontSize=23,
            leading=28,
            textColor=colors.HexColor("#173B6C"),
            spaceAfter=5 * mm,
        )
        meta_style = ParagraphStyle(
            "HomeworkMeta",
            parent=styles["Normal"],
            fontName="HomeworkRegular",
            fontSize=10.5,
            leading=15,
            textColor=colors.HexColor("#526172"),
            spaceAfter=2 * mm,
        )
        body_style = ParagraphStyle(
            "HomeworkBody",
            parent=styles["BodyText"],
            fontName="HomeworkRegular",
            fontSize=13,
            leading=21,
            textColor=colors.HexColor("#172033"),
            spaceAfter=3.5 * mm,
            allowWidows=0,
            allowOrphans=0,
        )
        source_style = ParagraphStyle(
            "HomeworkSource",
            parent=body_style,
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor("#66768A"),
            borderColor=colors.HexColor("#D9E2F1"),
            borderWidth=0.7,
            borderPadding=8,
            backColor=colors.HexColor("#F5F8FC"),
        )

        story = [
            Paragraph("Домашнее задание", title_style),
            Paragraph(f"<b>Ученик:</b> {html.escape(student)}", meta_style),
        ]
        if deadline:
            story.append(Paragraph(f"<b>Срок:</b> {html.escape(deadline)}", meta_style))
        story.extend([Spacer(1, 4 * mm)])

        normalized_text = text.replace("\\\\[", "\\[").replace("\\\\]", "\\]")
        for raw_block in re.split(r"\n\s*\n", normalized_text):
            block = raw_block.strip()
            if not block:
                continue
            block_math = BLOCK_MATH_RE.match(block)
            if block_math:
                formula = block_math.group(1) or block_math.group(2) or ""
                try:
                    image_path, width, height = self._formula_image(formula)
                    scale = min(1.8, 450 / max(width, 1))
                    formula_flowable = Image(
                        str(image_path), width=width * scale, height=height * scale
                    )
                    formula_flowable.hAlign = "CENTER"
                    story.extend([formula_flowable, Spacer(1, 4 * mm)])
                    continue
                except Exception:
                    block = formula

            style = source_style if block.casefold().startswith("источник:") else body_style
            story.append(Paragraph(self._inline_markup(block), style))

        document = SimpleDocTemplate(
            str(path),
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=20 * mm,
            bottomMargin=23 * mm,
            title=f"Домашнее задание #{homework_id}",
            author="Homework Agent",
        )
        document.build(story, onFirstPage=self._page, onLaterPages=self._page)
        return path
