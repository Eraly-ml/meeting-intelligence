from __future__ import annotations

import csv
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .schemas import JobResult, MeetingProtocol, Transcript


def _safe_csv(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def export_json(path: Path, protocol: MeetingProtocol, transcript: Transcript) -> None:
    path.write_text(
        json.dumps(
            {"protocol": protocol.model_dump(mode="json"), "transcript": transcript.model_dump(mode="json")},
            ensure_ascii=False, indent=2,
        ), encoding="utf-8",
    )


def export_csv(path: Path, protocol: MeetingProtocol) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([
            "assignee", "task", "deadline", "deadline_text", "priority",
            "speaker", "start", "end", "quote", "source_check", "review_status",
        ])
        for item in protocol.action_items:
            writer.writerow([_safe_csv(value) for value in (
                item.assignee, item.task, item.deadline_date, item.deadline_text,
                item.priority, item.evidence.speaker, item.evidence.start,
                item.evidence.end, item.evidence.quote, item.source_check,
                item.review_status,
            )])


def export_pdf(path: Path, protocol: MeetingProtocol, font_path: str | None = None) -> None:
    font_name = _pdf_font(font_path)
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font_name
    story = [Paragraph(escape(protocol.metadata.title), styles["Title"]), Spacer(1, 12)]
    sections = [
        ("Executive summary", protocol.executive_summary),
        ("Decisions", [_qualified(item.text, item) for item in protocol.decisions]),
        ("Open questions", [_qualified(item.text, item) for item in protocol.open_questions]),
        ("Risks", [_qualified(item.text, item) for item in protocol.risks]),
    ]
    for title, lines in sections:
        story.append(Paragraph(title, styles["Heading2"]))
        story.extend(Paragraph(f"• {escape(line)}", styles["BodyText"]) for line in lines)
        story.append(Spacer(1, 8))
    story.append(Paragraph("Action items", styles["Heading2"]))
    rows = [["Assignee", "Task", "Deadline", "Priority"]]
    rows.extend([
        [Paragraph(escape(item.assignee or "—"), styles["BodyText"]),
         Paragraph(escape(_qualified(item.task, item)), styles["BodyText"]),
         Paragraph(escape(item.deadline_text or "—"), styles["BodyText"]), item.priority]
        for item in protocol.action_items
    ])
    table = Table(rows, repeatRows=1, colWidths=[90, 250, 90, 70])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DCEAF7")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), font_name),
    ]))
    story.append(table)
    story.append(Spacer(1, 12))
    story.append(Paragraph("Evidence references", styles["Heading2"]))
    for item in protocol.decisions + protocol.action_items:
        quote = item.evidence.quote or "No verified source excerpt"
        citation = "{}: {} — {}".format(item.id, ", ".join(item.evidence.segment_ids) or "No references", quote)
        story.append(Paragraph(escape(citation), styles["BodyText"]))
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=36, rightMargin=36).build(story)


def _qualified(text, item):
    if item.review_status == "rejected":
        return "[Rejected] " + text
    if item.review_status == "needs_review" or item.source_check in {"failed", "unavailable"}:
        return "[Needs review] " + text
    return text


def _pdf_font(font_path: str | None = None) -> str:
    """Register a Unicode font available on macOS, Linux/Radxa, or Windows."""
    candidates = [Path(font_path)] if font_path else [
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            if "MeetingUnicode" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("MeetingUnicode", str(candidate)))
            return "MeetingUnicode"
    raise RuntimeError("No Unicode PDF font found; install DejaVu Sans or Arial")
