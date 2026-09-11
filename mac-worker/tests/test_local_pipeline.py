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


def test_real_fake_executables(tmp_path):
    ffmpeg, whisper = tmp_path / 'ffmpeg', tmp_path / 'whisper'
    ffmpeg.write_text('#!' + sys.executable + '\nimport sys,wave\nwith wave.open(sys.argv[-1],"wb") as w:\n w.setparams((1,2,16000,0,"NONE","not compressed"))\n w.writeframes(b"\\0" * 32000)\n')
    whisper.write_text('#!' + sys.executable + '\nimport sys,json,pathlib\np=pathlib.Path(sys.argv[sys.argv.index("-of")+1]+".json")\np.write_text(json.dumps({"transcription":[{"text":"Есеп дайын","offsets":{"from":0,"to":900}}]}))\n')
    ffmpeg.chmod(0o700)
    whisper.chmod(0o700)
    model, audio = tmp_path / 'model.bin', tmp_path / 'test.wav'
    model.write_bytes(b'fake-model')
    audio.write_bytes(b'fake-audio')
    settings = config(tmp_path, ffmpeg_binary=str(ffmpeg), whisper_binary=str(whisper), whisper_model=str(model))
    transcript = get_adapter('whisper-cpp', settings).transcribe(audio, 'auto')
    assert transcript.segments[0].text == 'Есеп дайын'
    assert transcript.segments[0].end == 0.9
    assert transcript.segments[0].speaker is None
    assert list((tmp_path / 'work').iterdir()) == []


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
        return httpx.Response(200, json={'done': True, 'message': {'content': report.model_dump_json()}})
    original = httpx.Client
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))
    with patch('meeting_worker.protocol.httpx.Client', client):
        result = call_ollama(transcript, JobManifest(meeting_id='test'), settings)
    assert result.action_items[0].source_check == 'failed'
    assert result.action_items[0].review_status == 'needs_review'
    assert len(requests) == 3


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
