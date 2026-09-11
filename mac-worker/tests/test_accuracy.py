import importlib.util
from pathlib import Path

import pytest

from meeting_worker.local_audio import AudioError, parse_whisper
from meeting_worker.schemas import JobManifest, TranscriptSegment
from meeting_worker.schemas import ActionItem, Evidence, MeetingMetadata, MeetingProtocol, Transcript
from meeting_worker.protocol import derive_summary, validate_evidence


def test_uncertain_wordpieces_are_preserved_without_rewriting_speech():
    item = {'text': 'Тимур sends it.', 'offsets': {'from': 100, 'to': 900}, 'tokens': [
        {'text': '[_BEG_]', 'p': 0.01}, {'text': 'Тим', 'p': 0.3}, {'text': 'ур', 'p': 0.8},
        {'text': ' sends', 'p': 0.95}, {'text': ' it.', 'p': 0.99}]}
    result = parse_whisper({'transcription': [item]}, 1, [])[0]
    segment = TranscriptSegment(id='s1', **{k: result[k] for k in ('text', 'start', 'end', 'tokens', 'needs_review')})
    assert segment.text == 'Тимур sends it.' and segment.needs_review
    assert ''.join(t.text for t in segment.tokens) == segment.text
    assert segment.tokens[0].probability == 0.3
    item['tokens'][1]['p'] = float('nan')
    with pytest.raises(AudioError, match='probabilities'):
        parse_whisper({'transcription': [item]}, 1, [])


def test_unknown_confidence_and_language_controls():
    result = parse_whisper({'transcription': [{'text': 'Hello', 'offsets': {'from': 0, 'to': 100}}]}, 1, [])[0]
    assert result['tokens'] == [] and not result['needs_review']
    for language in ('kk', 'ru', 'en', 'auto', 'kk_ru'):
        assert JobManifest(meeting_id='test', language_mode=language, vocabulary='Тимур, Radxa').vocabulary == 'Тимур, Radxa'
    with pytest.raises(ValueError):
        JobManifest(meeting_id='test', vocabulary='x' * 401)


def test_audio_uncertainty_stays_visible_without_hiding_supported_meaning():
    transcript = Transcript(model='fixture', raw_text='Timur sends it Monday.', segments=[
        TranscriptSegment(id='s1', text='Timur sends it Monday.', needs_review=True)])
    report = MeetingProtocol(metadata=MeetingMetadata(), action_items=[ActionItem(id='a1',
        task='Send it', assignee='Timur', deadline_text='Monday', evidence=Evidence(segment_ids=['s1']))])
    report = validate_evidence(report, transcript)
    assert report.action_items[0].source_check == 'passed'
    assert report.action_items[0].review_status == 'unreviewed'
    assert report.action_items[0].audio_warning
    derive_summary(report, JobManifest(meeting_id='test'), 'en')
    assert report.executive_summary
    assert all(source.audio_warning for source in report.executive_summary_sources)


def test_wer_counts_substitution_deletion_insertion_and_preserves_kazakh():
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'measure-wer.py'
    spec = importlib.util.spec_from_file_location('wer', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.score('Dana sends it Monday', 'Dana sends it Tuesday')['substitutions'] == 1
    assert module.score('Dana sends it Monday', 'Dana sends Monday')['deletions'] == 1
    assert module.score('Dana sends it Monday', 'Dana really sends it Monday')['insertions'] == 1
    assert module.score('Қазақша, РУССКИЙ. English!', 'қазақша русский english')['wer'] == 0
    assert module.score('Қазақша', 'Казакша')['wer'] == 1
    with pytest.raises(ValueError):
        module.score('', 'invented speech')


@pytest.mark.parametrize('repeats,flagged', [(2, False), (3, True), (30, True)])
def test_recognition_loops_require_review_without_deleting_words(repeats, flagged):
    from meeting_worker.asr import flag_repetition
    transcript = Transcript(model='fixture', raw_text='I am not sure. ' * repeats,
        segments=[TranscriptSegment(id=f's{i}', text='I am not sure.') for i in range(repeats)])
    original = transcript.raw_text
    flag_repetition(transcript)
    flag_repetition(transcript)
    assert bool(transcript.warnings) is flagged
    assert len(transcript.warnings) <= 1
    assert all(s.needs_review == flagged for s in transcript.segments)
    assert len(transcript.segments) == repeats and transcript.raw_text == original


def test_short_or_interrupted_repetition_does_not_trigger_loop_warning():
    from meeting_worker.asr import flag_repetition
    segments = [TranscriptSegment(id=str(i), text=text) for i, text in enumerate([
        'Yes.', 'Yes.', 'Yes.', 'I agree with that.', 'One different point.', 'I agree with that.'])]
    transcript = Transcript(model='fixture', raw_text='', segments=segments)
    flag_repetition(transcript)
    assert not transcript.warnings
    assert not any(s.needs_review for s in transcript.segments)
