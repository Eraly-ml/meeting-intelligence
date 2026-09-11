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
        'Postpone the launch until the report is complete.',
        'Timur is responsible for the task “Send launch report”.', 'The deadline for “Send launch report” is Monday.']
    assert [item.item_id for item in protocol.executive_summary_sources] == ['decision_001', 'action_001', 'action_001']
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
    assert protocol.executive_summary == ['Отправить отчёт.', 'За задачу «Отправить отчёт» отвечает Тимур.',
                                           'Срок задачи «Отправить отчёт»: понедельник.']
    assert len(protocol.executive_summary_sources) == 3


def test_sparse_speech_summary_is_not_padded_with_invented_information():
    from meeting_worker.protocol import derive_summary
    from meeting_worker.schemas import JobManifest, ProtocolItem
    manifest = JobManifest(meeting_id='sparse')
    protocol = MeetingProtocol(metadata=MeetingMetadata(), decisions=[ProtocolItem(
        id='decision_001', text='Proceed with the test', source_check='passed', evidence=Evidence(segment_ids=['s1']))])
    derive_summary(protocol, manifest)
    assert protocol.executive_summary == ['Proceed with the test.']
    assert len(protocol.executive_summary_sources) == 1
    protocol.decisions.clear()
    derive_summary(protocol, manifest)
    assert protocol.executive_summary == [] and protocol.executive_summary_sources == []


def test_summary_keeps_five_fact_limit_with_aligned_sources():
    from meeting_worker.protocol import derive_summary
    from meeting_worker.schemas import JobManifest, ProtocolItem
    protocol = MeetingProtocol(metadata=MeetingMetadata(), decisions=[ProtocolItem(
        id=f'decision_{index}', text=f'Verified outcome {index}', source_check='passed', evidence=Evidence(segment_ids=[f's{index}']))
        for index in range(7)])
    derive_summary(protocol, JobManifest(meeting_id='many'))
    assert len(protocol.executive_summary) == len(protocol.executive_summary_sources) == 5
    assert [item.item_id for item in protocol.executive_summary_sources] == [f'decision_{index}' for index in range(5)]


def test_summary_balances_decisions_actions_and_risks():
    from meeting_worker.protocol import derive_summary
    from meeting_worker.schemas import JobManifest, ProtocolItem
    supported = lambda item_id, text: ProtocolItem(id=item_id, text=text, source_check='passed', evidence=Evidence(segment_ids=[item_id]))
    protocol = MeetingProtocol(metadata=MeetingMetadata(),
        decisions=[supported(f'd{index}', f'Decision {index}') for index in range(4)],
        action_items=[ActionItem(id=f'a{index}', task=f'Task {index}', assignee='Owner', source_check='passed', evidence=Evidence(segment_ids=[f'a{index}'])) for index in range(2)],
        risks=[supported('r1', 'Power loss could interrupt recording')])
    derive_summary(protocol, JobManifest(meeting_id='balanced'))
    assert [source.item_id for source in protocol.executive_summary_sources] == ['d0', 'a0', 'd1', 'a1', 'r1']


def test_citation_validation_rejects_reversed_negation():
    from meeting_worker.schemas import ProtocolItem
    transcript = Transcript(model='fixture', raw_text='Processing resumes without duplicates.', segments=[
        TranscriptSegment(id='s1', text='Processing resumes without duplicates.')])
    protocol = MeetingProtocol(metadata=MeetingMetadata(), risks=[ProtocolItem(
        id='r1', text='Processing could resume with duplicates.', evidence=Evidence(segment_ids=['s1']))])
    checked = validate_evidence(protocol, transcript)
    assert checked.risks[0].source_check == 'failed'
    assert checked.risks[0].review_status == 'needs_review'


def test_citation_validation_preserves_matching_negative_claim():
    from meeting_worker.schemas import ProtocolItem
    transcript = Transcript(model='fixture', raw_text='We are not adopting cloud transcription.', segments=[
        TranscriptSegment(id='s1', text='We are not adopting cloud transcription.')])
    protocol = MeetingProtocol(metadata=MeetingMetadata(), decisions=[ProtocolItem(
        id='d1', text='Cloud transcription was not adopted.', evidence=Evidence(segment_ids=['s1']))])
    assert validate_evidence(protocol, transcript).decisions[0].source_check == 'passed'


def test_citation_validation_does_not_reject_a_task_that_checks_for_absence():
    transcript = Transcript(model='fixture', raw_text='Test that processing resumes without duplicates.', segments=[
        TranscriptSegment(id='s1', text='Test that processing resumes without duplicates.')])
    protocol = MeetingProtocol(metadata=MeetingMetadata(), action_items=[ActionItem(
        id='a1', task='Check processing for duplicates', evidence=Evidence(segment_ids=['s1']))])
    assert validate_evidence(protocol, transcript).action_items[0].source_check == 'passed'


def test_citation_validation_never_preserves_unchecked_summary():
    transcript = Transcript(model='fixture', raw_text='Send the report.', segments=[TranscriptSegment(id='s1', text='Send the report.')])
    protocol = MeetingProtocol(metadata=MeetingMetadata(), executive_summary=['All other proposed tasks were explicitly cancelled.'])
    assert validate_evidence(protocol, transcript).executive_summary == []
