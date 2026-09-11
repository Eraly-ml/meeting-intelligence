import hashlib
import io
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from .audio import EngineUnavailable

_font_lock = threading.Lock()


def render_pdf(meeting, font_path):
    if not font_path or not Path(font_path).is_file():
        raise EngineUnavailable("PDF unavailable: set MEETINGBOX_PDF_FONT to an installed Unicode TrueType font")
    try:
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import SimpleDocTemplate, Paragraph
    except ImportError as exc:
        raise EngineUnavailable("PDF unavailable: install the engine reportlab dependency during provisioning") from exc

    font_name = "MeetingBox-" + hashlib.sha256(str(Path(font_path).resolve()).encode()).hexdigest()[:12]
    with _font_lock:
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font_name, font_path))
    body = ParagraphStyle("Body", fontName=font_name, fontSize=10, leading=15, spaceAfter=8,
                          alignment=TA_LEFT, textColor=colors.HexColor("#15232c"))
    title = ParagraphStyle("Title", parent=body, fontSize=22, leading=29, spaceAfter=18)
    heading = ParagraphStyle("Heading", parent=body, fontSize=13, leading=19, spaceBefore=14, spaceAfter=8)
    output = io.BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=48, rightMargin=48,
                                 topMargin=48, bottomMargin=48, title=meeting.get("title", "Meeting report"))
    story = []

    def add(text, style=body):
        # Escape all transcript/model content: it cannot create ReportLab image/URL tags.
        safe = escape(str(text)).replace("\n", "<br/>")
        story.append(Paragraph(safe, style))

    add(meeting.get("title", "Meeting report"), title)
    add("MeetingBox · Local meeting report")
    report = meeting.get("report")
    if report:
        add("Summary", heading)
        add(report.get("summary", ""))
        add("Decisions", heading)
        for decision in report.get("decisions", []):
            add("• {} [segments {}]".format(decision["text"], ", ".join(map(str, decision["evidence"]))))
        add("Action items", heading)
        for item in report.get("action_items", []):
            add("• {} — Owner: {}; Due: {} [segments {}]".format(item["task"], item["owner"] or "Unassigned",
                item["due"] or "Unspecified", ", ".join(map(str, item["evidence"]))))
        for label, key in (("Open questions", "open_questions"), ("Topics", "topics"), ("Risks", "risks")):
            if report.get(key):
                add(label, heading)
                for item in report[key]:
                    add("• " + item)
    add("Transcript", heading)
    for item in meeting.get("segments", []):
        add("[{}] {:.2f}–{:.2f}s {}: {}".format(item["sequence"], item["start"], item["end"], item["speaker"], item["text"]))
    document.build(story)
    return output.getvalue()
