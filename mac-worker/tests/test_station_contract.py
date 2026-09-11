import asyncio
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'station' / 'src'))
from meeting_station.config import Settings as StationSettings
from meeting_station.main import create_app as create_station
from meeting_station.worker import MacClient
from meeting_worker.config import Settings as MacSettings
from meeting_worker.main import create_app as create_mac
from meeting_worker.schemas import ActionItem, Evidence, MeetingMetadata, MeetingProtocol


def test_actual_station_worker_multipart_and_cached_exports(tmp_path):
    async def scenario():
        worker_token = 'worker-contract-test-token'
        station_token = 'station-contract-test-token'
        mac = create_mac(MacSettings(_env_file=None, data_dir=tmp_path / 'mac', api_token=worker_token), start_worker=False)
        station_settings = StationSettings(token=station_token, worker_token=worker_token,
                                           data_dir=tmp_path / 'station', poll_seconds=0.001)
        remote = MacClient(station_settings, transport=httpx.ASGITransport(app=mac))
        station = create_station(station_settings, mac=remote, start_worker=False)
        report = MeetingProtocol(metadata=MeetingMetadata(title='Planning'), action_items=[ActionItem(
            id='a1', task='Send report', assignee='Dana', deadline_text='Monday', source_check='passed',
            evidence=Evidence(segment_ids=['seg_00001']))])
        async with mac.router.lifespan_context(mac), station.router.lifespan_context(station):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=station), base_url='http://station',
                                          headers={'Authorization': 'Bearer ' + station_token}) as client:
                job_id = str(uuid.uuid4())
                response = await client.post('/v1/jobs', headers={'Idempotency-Key': job_id}, data={
                    'manifest_json': json.dumps({'meeting_id': job_id, 'title': 'Planning'}),
                    'transcript': 'Dana will send the report Monday.'})
                assert response.status_code == 202, response.text
                assert await station.state.worker.run_once()
                mac_job = mac.state.store.get(job_id)
                assert mac_job is not None and mac_job.stage == 'queued'
                # A network retry carries the same normalized manifest and hash.
                replay = await remote.upload(station.state.store.get(job_id), station.state.store.source(job_id))
                assert replay['id'] == job_id
                assert len(mac.state.store.list()) == 1
                assert mac.state.store.claim().id == job_id
                with patch('meeting_worker.pipeline.call_ollama', return_value=report):
                    await asyncio.to_thread(mac.state.pipeline.run, job_id)
                assert mac.state.store.get(job_id).stage == 'completed'
                station.state.store.db.execute('UPDATE jobs SET available_at=0')
                assert await station.state.worker.run_once()
                result = (await client.get('/v1/jobs/' + job_id + '/result')).json()
                assert result['transcript']['raw_text'] == 'Dana will send the report Monday.'
                assert result['protocol']['action_items'][0]['assignee'] == 'Dana'
                pdf = await client.get('/v1/jobs/' + job_id + '/export/pdf')
                assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF-')
                calendar = await client.get('/v1/jobs/' + job_id + '/export/ics')
                assert calendar.status_code == 200 and b'BEGIN:VTODO' in calendar.content
                assert (await client.get('/v1/jobs/' + job_id)).json()['stage'] == 'completed'
    asyncio.run(scenario())
