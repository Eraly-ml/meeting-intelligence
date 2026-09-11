import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from meeting_worker.config import Settings
from meeting_worker.main import create_app
from meeting_worker.schemas import JobManifest, JobRecord, JobStage
from meeting_worker.store import JobStore

TOKEN = 'canonical-worker-test-token'
AUTH = {'Authorization': 'Bearer ' + TOKEN}
MANIFEST = {'meeting_id': 'meeting-fixture', 'title': 'Meeting'}


def settings(tmp_path, **kwargs):
    return Settings(_env_file=None, data_dir=tmp_path, api_token=TOKEN, **kwargs)


def submit(client, text='Dana sends report Monday.', job_id=None, **kwargs):
    headers = dict(AUTH)
    if job_id:
        headers['Idempotency-Key'] = job_id
    return client.post('/v1/jobs', headers=headers, data={'manifest_json': json.dumps(MANIFEST), 'transcript': text}, **kwargs)


def test_auth_traversal_and_replay(tmp_path):
    with TestClient(create_app(settings(tmp_path), start_worker=False)) as client:
        assert client.post('/v1/jobs', data={'x': 'y'}).status_code == 401
        assert submit(client, job_id='../../outside').status_code == 422
        job_id = str(uuid.uuid4())
        first = submit(client, job_id=job_id)
        assert first.status_code == 202
        assert first.json()['stage'] == 'queued'
        assert submit(client, job_id=job_id).json()['id'] == job_id
        assert submit(client, text='Different content', job_id=job_id).status_code == 409
        assert len(list((tmp_path / 'sources').iterdir())) == 1
        assert Path(first.json()['source_path']).read_text() == 'Dana sends report Monday.'
        assert client.get('/v1/jobs/' + job_id + '/result', headers=AUTH).status_code == 409


def test_file_size_empty_and_cleanup(tmp_path):
    with TestClient(create_app(settings(tmp_path, max_upload_bytes=8), start_worker=False)) as client:
        assert submit(client, text='too many bytes').status_code == 413
        assert submit(client, text='   ').status_code == 400
        response = client.post('/v1/jobs', headers=AUTH, data={'manifest_json': json.dumps(MANIFEST)},
                               files={'audio': ('audio.mp3', b'123456789', 'audio/mpeg')})
        assert response.status_code == 413
        assert list((tmp_path / 'sources').iterdir()) == []


def test_restart_keeps_queued_marks_active_failed(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config, start_worker=False)) as client:
        one, two = submit(client).json(), submit(client).json()
        client.app.state.store.update(two['id'], JobStage.TRANSCRIBING)
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.get('/v1/jobs/' + one['id'], headers=AUTH).json()['stage'] == 'queued'
        failed = client.get('/v1/jobs/' + two['id'], headers=AUTH).json()
        assert failed['stage'] == 'failed'
        assert client.post('/v1/jobs/' + two['id'] + '/retry', headers=AUTH).json()['stage'] == 'queued'


def test_durable_worker_health_and_single_consumer(tmp_path):
    started, release = threading.Event(), threading.Event()
    seen = []
    class Pipeline:
        def __init__(self, config, store):
            self.store = store
        def run(self, job_id):
            seen.append(job_id)
            started.set()
            assert release.wait(5)
            self.store.update(job_id, JobStage.FAILED)
    with TestClient(create_app(settings(tmp_path), pipeline_factory=Pipeline)) as client:
        job = submit(client).json()
        assert started.wait(3)
        try:
            assert client.get('/health').status_code == 200
            assert client.get('/v1/jobs/' + job['id'], headers=AUTH).json()['stage'] == 'preprocessing'
        finally:
            release.set()
    assert seen == [job['id']]


def test_model_and_token_policy(tmp_path):
    with pytest.raises(ValueError, match='MI_API_TOKEN'):
        create_app(Settings(_env_file=None, data_dir=tmp_path))
    with pytest.raises(ValueError, match='loopback'):
        settings(tmp_path, ollama_url='http://192.168.8.57:11434')
    with pytest.raises(ValueError, match='local'):
        settings(tmp_path, ollama_model='qwen3.5:cloud')
    with pytest.raises(ValueError, match='downloads'):
        create_app(settings(tmp_path, hf_offline=False))


def test_cancellation_cannot_be_overwritten(tmp_path):
    with TestClient(create_app(settings(tmp_path), start_worker=False)) as client:
        job = submit(client).json()
        client.post('/v1/jobs/' + job['id'] + '/cancel', headers=AUTH)
        result = client.app.state.store.update(job['id'], JobStage.COMPLETED)
        assert result.stage == JobStage.CANCELLED
