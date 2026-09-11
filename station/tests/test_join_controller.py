import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_browser import wav_bytes


@pytest.mark.parametrize('states,admitted', [(['waiting','joined','ended'],True), (['blocked'],False), (['waiting','ended'],False)])
def test_controller_distinguishes_admission_from_waiting_and_preserves_audio(tmp_path, monkeypatch, states, admitted):
    path = Path(__file__).parents[2] / 'deploy/meeting-browser/controller.py'
    spec = importlib.util.spec_from_file_location('join_controller', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    controller = module.Controller(tmp_path)
    job_id = str(uuid4())
    controller.save({'id':job_id,'state':'recording','error':None})
    controller.path(job_id,'.wav').write_bytes(wav_bytes())
    controller.active = job_id
    controller.process = SimpleNamespace(poll=lambda:0,returncode=0)
    controller.join_state = {'recording_id':job_id,'state':'joining','admitted':False}
    steps = iter(states)
    monkeypatch.setattr(controller,'observe_join',lambda: {'state':next(steps),'message':'fixture state'})
    calls = []
    monkeypatch.setattr(controller,'cdp',lambda *args:calls.append(args))
    monkeypatch.setattr(module.time,'sleep',lambda *args:None)
    controller.automate(job_id)
    result = controller.record(job_id)
    assert controller.active is None
    assert controller.join_state['admitted'] is admitted
    assert result['state'] == ('stopped' if admitted else 'interrupted')
    assert bool(result['error']) is not admitted
    assert calls == [('Page.navigate',{'url':'about:blank'})]
    assert controller.path(job_id,'.wav').read_bytes() == wav_bytes()
