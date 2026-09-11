from meeting_worker.diarization import SpeakerTurn, assign_speakers
from meeting_worker.schemas import Transcript, TranscriptSegment


def test_assigns_speaker_with_largest_overlap():
    transcript = Transcript(
        language="ru", model="fixture", raw_text="Привет",
        segments=[TranscriptSegment(id="seg_1", start=2, end=6, text="Привет")],
    )
    turns = [SpeakerTurn(0, 3, "A"), SpeakerTurn(3, 7, "B")]
    assert assign_speakers(transcript, turns).segments[0].speaker == "B"
