import csv
import json

from meeting_worker.exports import export_csv, export_json, export_pdf
from meeting_worker.schemas import (
    ActionItem, Evidence, MeetingMetadata, MeetingProtocol, Transcript, TranscriptSegment,
)


def fixture_data():
    transcript = Transcript(
        language="kk", model="fixture", raw_text="Айбек есепті жұмаға дайындайды.",
        segments=[TranscriptSegment(id="seg_00001", text="Айбек есепті жұмаға дайындайды.")],
    )
    protocol = MeetingProtocol(
        metadata=MeetingMetadata(title="Қазақша кездесу"),
        executive_summary=["Есепті дайындау келісілді."],
        action_items=[ActionItem(
            id="action_01", task="=HYPERLINK(\"bad\")", assignee="Айбек",
            deadline_text="жұма", evidence=Evidence(segment_ids=["seg_00001"]),
        )],
    )
    return protocol, transcript


def test_exports_are_unicode_and_safe(tmp_path):
    protocol, transcript = fixture_data()
    json_path, csv_path, pdf_path = tmp_path / "m.json", tmp_path / "a.csv", tmp_path / "m.pdf"
    export_json(json_path, protocol, transcript)
    export_csv(csv_path, protocol)
    export_pdf(pdf_path, protocol)

    assert json.loads(json_path.read_text(encoding="utf-8"))["protocol"]["metadata"]["title"] == "Қазақша кездесу"
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[1][1].startswith("'=")
    assert pdf_path.read_bytes().startswith(b"%PDF")
