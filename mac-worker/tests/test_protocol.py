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
