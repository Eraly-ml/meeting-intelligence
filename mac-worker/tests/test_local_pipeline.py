import json
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from meeting_worker.asr import get_adapter
from meeting_worker.config import Settings
from meeting_worker.protocol import call_ollama, validate_evidence
from meeting_worker.schemas import ActionItem, Evidence, JobManifest, MeetingMetadata, MeetingProtocol, Transcript, TranscriptSegment


def config(tmp_path, **kwargs):
    value = Settings(_env_file=None, data_dir=tmp_path, api_token='test-token-local-pipeline', **kwargs)
    value.prepare()
    return value


@pytest.mark.parametrize('vad', [False, True])
def test_real_fake_executables(tmp_path, vad):
    ffmpeg, whisper = tmp_path / 'ffmpeg', tmp_path / 'whisper'
    ffmpeg.write_text('#!' + sys.executable + '\nimport sys,wave\nwith wave.open(sys.argv[-1],"wb") as w:\n w.setparams((1,2,16000,0,"NONE","not compressed"))\n w.writeframes(b"\\0" * 32000)\n')
    whisper.write_text('#!' + sys.executable + '\nimport sys,json,pathlib\nassert sys.argv[sys.argv.index("-mc")+1] == "0"\nassert ("--vad" in sys.argv) == ' + repr(vad) + '\nif "--vad" in sys.argv: assert pathlib.Path(sys.argv[sys.argv.index("--vad-model")+1]).is_file()\np=pathlib.Path(sys.argv[sys.argv.index("-of")+1]+".json")\np.write_text(json.dumps({"transcription":[{"text":"Есеп дайын","offsets":{"from":0,"to":900}}]}))\n')
    ffmpeg.chmod(0o700)
    whisper.chmod(0o700)
    model, audio = tmp_path / 'model.bin', tmp_path / 'test.wav'
    model.write_bytes(b'fake-model')
    audio.write_bytes(b'fake-audio')
    settings = config(tmp_path, ffmpeg_binary=str(ffmpeg), whisper_binary=str(whisper), whisper_model=str(model), whisper_vad_model=str(model) if vad else '')
    transcript = get_adapter('whisper-cpp', settings).transcribe(audio, 'auto')
    assert transcript.segments[0].text == 'Есеп дайын'
    assert transcript.segments[0].end == 0.9
    assert transcript.segments[0].speaker is None
    assert list((tmp_path / 'work').iterdir()) == []


def test_missing_configured_vad_model_is_not_silently_ignored(tmp_path):
    from meeting_worker.asr import local_audio_settings
    from meeting_worker.local_audio import AudioProcessor, EngineUnavailable
    model = tmp_path / 'model.bin'
    model.write_bytes(b'fixture')
    settings = config(tmp_path, ffmpeg_binary=sys.executable, whisper_binary=sys.executable,
        whisper_model=str(model), whisper_vad_model=str(tmp_path / 'missing.bin'))
    processor = AudioProcessor(local_audio_settings(settings))
    assert processor.capabilities()['transcription']['ready'] is False
    with pytest.raises(EngineUnavailable, match='whisper_vad_model'):
        processor.transcribe(tmp_path / 'recording.wav', 'en')


def test_generated_human_review_and_calendar_date_removed():
    transcript = Transcript(model='fixture', raw_text='Friday', segments=[TranscriptSegment(id='s1', text='Dana sends it Friday.')])
    report = MeetingProtocol(metadata=MeetingMetadata(), action_items=[ActionItem(id='a1', task='Send report',
        assignee='Dana', deadline_text='Friday', deadline_date='2026-09-11', review_status='human_confirmed', evidence=Evidence(segment_ids=['s1']))])
    output = validate_evidence(report, transcript)
    assert output.action_items[0].deadline_date is None
    assert output.action_items[0].review_status == 'needs_review'


def test_local_model_semantic_review_flags_unsupported(tmp_path):
    settings = config(tmp_path)
    transcript = Transcript(model='fixture', raw_text='Dana sends the report Monday.', segments=[TranscriptSegment(id='s1', text='Dana sends the report Monday.')])
    report = MeetingProtocol(metadata=MeetingMetadata(), action_items=[ActionItem(id='a1', task='Send report', assignee='Dana', evidence=Evidence(segment_ids=['s1']))])
    requests = []
    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'model_info': {'x': 1}, 'details': {'format': 'gguf'}})
        if 'verdicts' in payload['format']['properties']:
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps({'verdicts': [{'id': 0, 'supported': False}]})}})
        if 'summary' in payload['format']['properties']:
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps({'title': 'Report delivery', 'summary': [], 'topics': []})}})
        return httpx.Response(200, json={'done': True, 'message': {'content': report.model_dump_json()}})
    original = httpx.Client
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))
    with patch('meeting_worker.protocol.httpx.Client', client):
        result = call_ollama(transcript, JobManifest(meeting_id='test'), settings)
    assert result.action_items[0].source_check == 'failed'
    assert result.action_items[0].review_status == 'needs_review'
    assert len(requests) == 4


def test_generation_schema_requires_grounded_refs_and_no_inferred_date():
    from meeting_worker.protocol import _raw_schema
    schema = _raw_schema([TranscriptSegment(id='s1', text='Dana sends it Monday.')], None)
    evidence = schema['$defs']['Evidence']['properties']['segment_ids']
    assert evidence['minItems'] == 1 and evidence['items']['enum'] == ['s1']
    assert schema['$defs']['ActionItem']['properties']['deadline_date']['enum'] == [None]
    assert 'deadline_text' in schema['$defs']['ActionItem']['required']
    assert 'review_status' not in schema['$defs']['ActionItem']['properties']


def test_invalid_citations_clear_derived_claims():
    transcript = Transcript(model='fixture', raw_text='', segments=[])
    report = MeetingProtocol(metadata=MeetingMetadata(), action_items=[ActionItem(id='a1', task='Send report',
        deadline_date='2024-01-08', evidence=Evidence(segment_ids=['invented'], start=100, end=200, speaker='Fake'))])
    item = validate_evidence(report, transcript).action_items[0]
    assert item.deadline_date is None
    assert item.evidence.start is None and item.evidence.end is None and item.evidence.speaker is None


def test_fabricated_dates_in_text_are_removed_and_flagged():
    from meeting_worker.schemas import Topic
    transcript = Transcript(model='fixture', raw_text='Monday', segments=[TranscriptSegment(id='s1', text='Send it Monday.')])
    report = MeetingProtocol(metadata=MeetingMetadata(), executive_summary=['Send it 2024-05-27'],
        topics=[Topic(id='t1', title='Send report Monday 2024-05-27', text='Report', evidence=Evidence(segment_ids=['s1']))],
        action_items=[ActionItem(id='a1', task='Send report', assignee='null')])
    result = validate_evidence(report, transcript)
    assert '2024-05-27' not in result.model_dump_json()
    assert result.topics[0].review_status == 'needs_review'
    assert result.action_items[0].assignee is None


@pytest.mark.parametrize('semantic', [True, False])
def test_unsolicited_model_summary_cannot_bypass_fact_review(tmp_path, semantic):
    from meeting_worker.schemas import ProtocolItem
    settings = config(tmp_path, semantic_verification=semantic)
    transcript = Transcript(model='fixture', raw_text='Timur will send the report Monday. We decided to postpone the launch until the report is complete.', segments=[
        TranscriptSegment(id='s1', text='Timur will send the report Monday.'),
        TranscriptSegment(id='s2', text='We decided to postpone the launch until the report is complete.')])
    report = MeetingProtocol(metadata=MeetingMetadata(),
        executive_summary=['All other proposed tasks were explicitly cancelled or superseded.'],
        decisions=[ProtocolItem(id='d', text='Postpone the launch until the report is complete.', evidence=Evidence(segment_ids=['s2']))],
        action_items=[ActionItem(id='a', task='Send report', assignee='Timur', deadline_text='Monday', evidence=Evidence(segment_ids=['s1']))])
    reviewed = []

    def handler(request):
        payload = json.loads(request.content)
        if request.url.path == '/api/show':
            return httpx.Response(200, json={'model_info': {'x': 1}, 'details': {'format': 'gguf'}})
        if 'verdicts' in payload['format']['properties']:
            claims = json.loads(payload['messages'][1]['content'])
            reviewed.extend(claims)
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps({'verdicts': [
                {'id': claim['id'], 'supported': 'cancelled' not in claim['claim'].get('text', '')} for claim in claims]})}})
        if 'summary' in payload['format']['properties']:
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps({
                'title': 'Report delivery and launch timing', 'topics': [], 'summary': [
                    {'id': 'x', 'text': 'The launch waits for the report.', 'evidence': {'segment_ids': ['s2']}},
                    {'id': 'y', 'text': 'Timur will send the report Monday.', 'evidence': {'segment_ids': ['s1']}},
                    {'id': 'z', 'text': 'All other tasks were cancelled.', 'evidence': {'segment_ids': ['s1']}}]})}})
        assert 'executive_summary' not in payload['format']['properties']
        assert 'executive_summary_sources' not in payload['format']['properties']
        return httpx.Response(200, json={'done': True, 'message': {'content': report.model_dump_json()}})

    original = httpx.Client
    with patch('meeting_worker.protocol.httpx.Client', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler))):
        result = call_ollama(transcript, JobManifest(meeting_id='fixture'), settings)
    assert not any('cancelled' in line or 'superseded' in line for line in result.executive_summary)
    if semantic:
        assert {claim['kind'] for claim in reviewed} == {'decision', 'action', 'summary'}
        assert result.executive_summary == ['The launch waits for the report.', 'Timur will send the report Monday.']
        assert result.executive_summary_sources[0].evidence.segment_ids == ['s2']
    else:
        assert not reviewed and result.executive_summary == []
        assert result.executive_summary_sources == []


def test_previous_protocol_never_feeds_unchecked_summary_back_into_reconciliation():
    from meeting_worker.protocol import _messages
    previous = MeetingProtocol(metadata=MeetingMetadata(), executive_summary=['All other tasks are cancelled.'])
    payload = json.loads(_messages([TranscriptSegment(id='s1', text='One explicit action.')], JobManifest(meeting_id='fixture'), previous)[1]['content'])
    assert 'executive_summary' not in payload['previous_protocol']
    assert 'executive_summary_sources' not in payload['previous_protocol']
