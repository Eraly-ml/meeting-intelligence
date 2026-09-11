from meeting_worker.protocol import validate_evidence
from meeting_worker.schemas import (
    ActionItem, Evidence, MeetingMetadata, MeetingProtocol, Transcript, TranscriptSegment,
)


def test_evidence_is_copied_from_transcript() -> None:
    transcript = Transcript(
        language="ru", model="fixture", raw_text="Айбек подготовит отчёт.",
        segments=[TranscriptSegment(
            id="seg_00001", start=10, end=13, speaker="SPEAKER_01",
            text="Айбек подготовит отчёт.",
        )],
    )
    protocol = MeetingProtocol(
        metadata=MeetingMetadata(),
        action_items=[ActionItem(
            id="action_01", task="Подготовить отчёт", assignee="Айбек",
            evidence=Evidence(segment_ids=["seg_00001"]),
        )],
    )

    checked = validate_evidence(protocol, transcript)
    item = checked.action_items[0]
    assert item.source_check == "passed"
    assert item.evidence.quote == "Айбек подготовит отчёт."
    assert item.evidence.start == 10
    assert item.evidence.speaker == "SPEAKER_01"


def test_missing_evidence_requires_review() -> None:
    transcript = Transcript(language="kk", model="fixture", raw_text="", segments=[])
    protocol = MeetingProtocol(
        metadata=MeetingMetadata(),
        action_items=[ActionItem(id="action_01", task="Есеп дайындау")],
    )
    checked = validate_evidence(protocol, transcript)
    assert checked.action_items[0].source_check == "unavailable"
    assert checked.action_items[0].review_status == "needs_review"


def test_summary_copies_only_reviewed_facts_with_matching_provenance():
    from meeting_worker.protocol import derive_summary
    from meeting_worker.schemas import JobManifest, ProtocolItem
    protocol = MeetingProtocol(metadata=MeetingMetadata(),
        executive_summary=['All other proposed tasks were explicitly cancelled.'],
        decisions=[ProtocolItem(id='decision_001', text='Postpone the launch until the report is complete.',
                                source_check='passed', evidence=Evidence(segment_ids=['s3'], quote='We decided to postpone the launch until the report is complete.'))],
        action_items=[ActionItem(id='action_001', task='Send launch report', assignee='Timur', deadline_text='Monday',
                                 source_check='passed', evidence=Evidence(segment_ids=['s2'], quote='Timur will send it Monday.'))],
        risks=[ProtocolItem(id='risk_001', text='All other proposed tasks were explicitly cancelled.',
                            source_check='failed', review_status='needs_review', evidence=Evidence(segment_ids=['s2']))])
    derive_summary(protocol, JobManifest(meeting_id='fixture', output_language='en'))
    assert protocol.executive_summary == [
        'Postpone the launch until the report is complete.', 'Send launch report · Owner: Timur · Due: Monday']
    assert [item.item_id for item in protocol.executive_summary_sources] == ['decision_001', 'action_001']
    assert protocol.executive_summary_sources[1].evidence.segment_ids == ['s2']
    protocol.action_items[0].evidence.segment_ids.append('later-mutation')
    assert protocol.executive_summary_sources[1].evidence.segment_ids == ['s2']


def test_summary_excludes_unavailable_and_needs_review_facts_and_keeps_output_language():
    from meeting_worker.protocol import derive_summary
    from meeting_worker.schemas import JobManifest, ProtocolItem
    protocol = MeetingProtocol(metadata=MeetingMetadata(),
        decisions=[ProtocolItem(id='d1', text='Unverified decision')],
        action_items=[ActionItem(id='a1', task='Отправить отчёт', assignee='Тимур', deadline_text='понедельник',
                                 source_check='passed', evidence=Evidence(segment_ids=['s1'])),
                      ActionItem(id='a2', task='Needs review', source_check='passed', review_status='needs_review')])
    derive_summary(protocol, JobManifest(meeting_id='fixture', output_language='same'), 'ru')
    assert protocol.executive_summary == ['Отправить отчёт · Ответственный: Тимур · Срок: понедельник']
    assert len(protocol.executive_summary_sources) == 1


def test_citation_validation_never_preserves_unchecked_summary():
    transcript = Transcript(model='fixture', raw_text='Send the report.', segments=[TranscriptSegment(id='s1', text='Send the report.')])
    protocol = MeetingProtocol(metadata=MeetingMetadata(), executive_summary=['All other proposed tasks were explicitly cancelled.'])
    assert validate_evidence(protocol, transcript).executive_summary == []
