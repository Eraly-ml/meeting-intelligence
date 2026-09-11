import csv
import json

from meeting_worker.exports import export_csv, export_ics, export_json, export_pdf
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


def test_pdf_contains_full_protocol_and_evidence_for_every_section(tmp_path):
    from pypdf import PdfReader
    from meeting_worker.protocol import derive_summary
    from meeting_worker.schemas import JobManifest, ProtocolItem, Topic

    def evidence(index, quote):
        return Evidence(segment_ids=[f'source_{index}'], quote=quote, start=index * 10, end=index * 10 + 3)

    protocol = MeetingProtocol(metadata=MeetingMetadata(title='Complete protocol', meeting_date='2026-09-11', timezone='Asia/Almaty'),
        decisions=[ProtocolItem(id='decision_001', text='Approve the pilot.', source_check='passed',
                                evidence=evidence(1, 'We agree to approve the pilot.'))],
        topics=[Topic(id='topic_001', title='Қаржы жоспары', text='Budget allocation was discussed.', source_check='passed',
                      evidence=evidence(2, 'The budget allocation is our next topic.'))],
        open_questions=[ProtocolItem(id='question_001', text='Which adapter should we use?', source_check='passed',
                                     evidence=evidence(3, 'We have not chosen an adapter.'))],
        action_items=[ActionItem(id='action_001', task='Send the pilot report', assignee='Dana', deadline_date='2026-09-14',
                                 source_check='passed', evidence=evidence(4, 'Dana will send the pilot report on 2026-09-14.'))],
        risks=[ProtocolItem(id='risk_001', text='Adapter throughput remains uncertain.', source_check='failed', review_status='needs_review',
                            evidence=evidence(5, 'We have not measured adapter throughput.'))])
    derive_summary(protocol, JobManifest(meeting_id='complete', output_language='en'))
    path = tmp_path / 'complete.pdf'
    export_pdf(path, protocol)
    reader = PdfReader(path)
    text = ' '.join('\n'.join(page.extract_text() for page in reader.pages).split())
    for heading in ('Executive summary', 'Decisions', 'Topics and key points', 'Open questions', 'Risks', 'Action items', 'Evidence references'):
        assert heading in text
    assert 'Қаржы жоспары' in text and 'Budget allocation was discussed.' in text
    assert 'Meeting date: 2026-09-11' in text and 'Timezone: Asia/Almaty' in text
    assert '2026-09-14' in text and '[Needs review] Adapter throughput remains uncertain.' in text
    for group in (protocol.decisions, protocol.topics, protocol.open_questions, protocol.action_items, protocol.risks):
        for item in group:
            assert item.id in text
            assert item.evidence.segment_ids[0] in text
            assert item.evidence.quote in text
    assert '[10.00–13.00s]' in text
    for index in range(1, len(protocol.executive_summary_sources) + 1):
        assert f'Summary {index}' in text


def test_ics_contains_only_source_checked_actions_and_explicit_dates(tmp_path):
    protocol = MeetingProtocol(metadata=MeetingMetadata(title='Launch, plan'), action_items=[
        ActionItem(id='a1', task='Send report; notify team', assignee='Айбек', deadline_text='2026-09-14',
                   deadline_date='2026-09-14', priority='high', source_check='passed',
                   evidence=Evidence(segment_ids=['s1'], quote='Айбек sends the report.')),
        ActionItem(id='a2', task='Arrange handover', deadline_text='next week', source_check='passed'),
        ActionItem(id='a3', task='Invented task', source_check='failed', review_status='needs_review'),
    ])
    path = tmp_path / 'actions.ics'
    export_ics(path, protocol)
    content = path.read_bytes()
    text = content.decode('utf-8')
    assert content.endswith(b'\r\n') and '\n' not in text.replace('\r\n', '')
    assert text.count('BEGIN:VTODO') == 2
    assert 'SUMMARY:Send report\\; notify team' in text
    assert 'Owner: Айбек' in text and 'DUE;VALUE=DATE:20260914' in text
    assert 'PRIORITY:3' in text and 'Spoken deadline: next week' in text
    assert 'Invented task' not in text
