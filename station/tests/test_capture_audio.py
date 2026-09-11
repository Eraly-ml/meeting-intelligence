"""Capture health must depend on PCM sound, not just a WAV header or byte count."""
import array
import importlib.util
import math
from pathlib import Path
import wave
from uuid import uuid4

import pytest


@pytest.fixture
def controller(tmp_path):
    source = Path(__file__).parents[2] / 'deploy/meeting-browser/controller.py'
    spec = importlib.util.spec_from_file_location('audio_controller', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.Controller(tmp_path)


def write_pcm(path, samples):
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(array.array('h', samples).tobytes())


def test_health_distinguishes_silence_sound_and_stopped_writes(controller, monkeypatch):
    module, capture = controller
    now = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: now[0])
    recording_id = str(uuid4())
    capture.save({'id': recording_id, 'state': 'recording', 'created_at': now[0]})
    capture.active = recording_id
    path = capture.path(recording_id, '.wav')
    samples = [0] * 16000
    write_pcm(path, samples)
    health = capture.record(recording_id)['audio_health']
    assert health['state'] == 'waiting' and not health['received']
    now[0] += 2
    samples.extend(round(4000 * math.sin(index * math.tau * 440 / 16000)) for index in range(16000))
    write_pcm(path, samples)
    health = capture.record(recording_id)['audio_health']
    assert health['state'] == 'receiving' and health['received']
    assert -30 < health['rms_dbfs'] < -10
    now[0] += 20
    write_pcm(path, samples + [0] * 80000)
    health = capture.record(recording_id)['audio_health']
    assert health['state'] == 'quiet' and health['quiet_seconds'] == 20
    assert health['rms_dbfs'] == -96
    now[0] += 16
    assert capture.record(recording_id)['audio_health']['state'] == 'stalled'


@pytest.mark.parametrize('sample,expected', [(0, 'interrupted'), (123, 'stopped')])
def test_empty_sound_is_reported_without_destroying_source(controller, sample, expected):
    _, capture = controller
    recording_id = str(uuid4())
    capture.save({'id': recording_id, 'state': 'recording'})
    path = capture.path(recording_id, '.wav')
    write_pcm(path, [0] * 16000 + [sample] * 16000)
    original = path.read_bytes()
    result = capture.finalize(recording_id)
    assert result['state'] == expected
    assert result['audio_received'] is bool(sample)
    assert ('No sound was captured' in (result['error'] or '')) is (sample == 0)
    assert path.read_bytes() == original
