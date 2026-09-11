from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
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


def export_ics(path: Path, protocol: MeetingProtocol) -> None:
    """Write source-checked actions as portable RFC 5545 VTODO entries."""
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Meeting Station//Action Items//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
    ]
    generated = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    priorities = {"urgent": 1, "high": 3, "medium": 5, "low": 7, "not_specified": 0}
    for item in protocol.action_items:
        if item.source_check != "passed" or item.review_status not in {"unreviewed", "human_confirmed"}:
            continue
        identity = "\0".join((protocol.metadata.title, str(protocol.metadata.meeting_date or ""), item.id, item.task))
        uid = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24] + "@meeting-station.local"
        details = ["Owner: " + (item.assignee or "Unassigned")]
        if item.deadline_text:
            details.append("Spoken deadline: " + item.deadline_text)
        if item.evidence.quote:
            details.append("Source: “" + item.evidence.quote + "”")
        lines.extend([
            "BEGIN:VTODO", "UID:" + uid, "DTSTAMP:" + generated,
            "SUMMARY:" + _ics_escape(item.task),
            "DESCRIPTION:" + _ics_escape("\n".join(details)),
            "PRIORITY:" + str(priorities.get(item.priority, 0)),
            "STATUS:NEEDS-ACTION",
        ])
        if item.deadline_date:
            lines.append("DUE;VALUE=DATE:" + item.deadline_date.strftime("%Y%m%d"))
        lines.append("END:VTODO")
    lines.append("END:VCALENDAR")
    path.write_bytes(("\r\n".join(_ics_fold(line) for line in lines) + "\r\n").encode("utf-8"))


def _ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def _ics_fold(line: str) -> str:
    """Fold a content line at 75 UTF-8 octets without splitting a character."""
    chunks, current, limit = [], "", 75
    for character in line:
        if current and len((current + character).encode("utf-8")) > limit:
            chunks.append(current)
            current, limit = character, 74
        else:
            current += character
    chunks.append(current)
    return "\r\n ".join(chunks)


def export_pdf(path: Path, protocol: MeetingProtocol, font_path: str | None = None, transcript: Transcript | None = None) -> None:
    font_name = _pdf_font(font_path)
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font_name
    story = [Paragraph(escape(protocol.metadata.title), styles["Title"]), Spacer(1, 12)]
    if protocol.metadata.meeting_date:
        story.append(Paragraph("Meeting date: " + protocol.metadata.meeting_date.isoformat(), styles["BodyText"]))
    if protocol.metadata.timezone:
        story.append(Paragraph("Timezone: " + escape(protocol.metadata.timezone), styles["BodyText"]))
    findings = protocol.decisions + protocol.topics + protocol.open_questions + protocol.action_items + protocol.risks
    if not findings:
        story.append(Paragraph("Report needs review", styles["Heading2"]))
        story.append(Paragraph("No structured meeting findings were extracted. This is not a complete meeting protocol. Check the transcript and original recording before relying on this report.", styles["BodyText"]))
    if transcript and transcript.warnings:
        story.append(Paragraph("Transcription needs review", styles["Heading2"]))
        story.extend(Paragraph(escape(warning), styles["BodyText"]) for warning in transcript.warnings)
    if not findings:
        _append_transcript(story, styles, transcript, new_page=False)
        SimpleDocTemplate(str(path), pagesize=A4, leftMargin=36, rightMargin=36).build(story)
        return
    sections = [
        ("Executive summary", protocol.executive_summary),
        ("Decisions", [_qualified(item.text, item) for item in protocol.decisions]),
        ("Topics and key points", [_qualified(item.title + ": " + item.text, item) for item in protocol.topics]),
        ("Open questions", [_qualified(item.text, item) for item in protocol.open_questions]),
        ("Risks", [_qualified(item.text, item) for item in protocol.risks]),
    ]
    for title, lines in sections:
        story.append(Paragraph(title, styles["Heading2"]))
        if lines:
            story.extend(Paragraph(f"• {escape(line)}", styles["BodyText"]) for line in lines)
        else:
            story.append(Paragraph("No source-checked summary is available." if title == "Executive summary" else "No items were extracted for this section.", styles["BodyText"]))
        story.append(Spacer(1, 8))
    story.append(Paragraph("Action items", styles["Heading2"]))
    rows = [["Assignee", "Task", "Deadline", "Priority"]]
    rows.extend([
        [Paragraph(escape(item.assignee or "—"), styles["BodyText"]),
         Paragraph(escape(_qualified(item.task, item)), styles["BodyText"]),
         Paragraph(escape(item.deadline_text or (item.deadline_date.isoformat() if item.deadline_date else "—")), styles["BodyText"]), item.priority]
        for item in protocol.action_items
    ])
    table = Table(rows, repeatRows=1, colWidths=[90, 250, 90, 70])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeec")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), font_name),
    ]))
    story.append(table)
    if not protocol.action_items:
        story.append(Paragraph("No action items were extracted. Owners and deadlines have not been invented.", styles["BodyText"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Evidence references", styles["Heading2"]))
    for index, source in enumerate(protocol.executive_summary_sources, 1):
        story.append(Paragraph(escape(f"Summary {index} → {source.item_id}"), styles["BodyText"]))
    for item in protocol.decisions + protocol.topics + protocol.open_questions + protocol.action_items + protocol.risks:
        quote = item.evidence.quote or "No verified source excerpt"
        citation = "{}: {} — {}".format(item.id, ", ".join(item.evidence.segment_ids) or "No references", quote)
        if item.evidence.start is not None and item.evidence.end is not None:
            citation += " [{:.2f}–{:.2f}s]".format(item.evidence.start, item.evidence.end)
        story.append(Paragraph(escape(citation), styles["BodyText"]))
    _append_transcript(story, styles, transcript, new_page=True)
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=36, rightMargin=36).build(story)


def _append_transcript(story, styles, transcript, new_page):
    if transcript is not None:
        story.extend([PageBreak() if new_page else Spacer(1, 14), Paragraph("Transcript", styles["Heading1"]),
            Paragraph("Speech recognition output for review. It may contain errors; timestamps refer to the original recording.", styles["BodyText"]), Spacer(1, 10)])
        for warning in transcript.warnings:
            story.append(Paragraph(escape(warning), styles["BodyText"]))
        for segment in transcript.segments:
            prefix = "[{:.2f}–{:.2f}s] ".format(segment.start, segment.end) if segment.start is not None and segment.end is not None else ""
            if segment.speaker:
                prefix += segment.speaker + ": "
            if segment.needs_review:
                prefix += "[Check audio] "
            story.append(Paragraph(escape(prefix + segment.text), styles["BodyText"]))
        if not transcript.segments:
            story.append(Paragraph(escape(transcript.raw_text or "No speech was recognized. Check the recording and input audio source."), styles["BodyText"]))


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
