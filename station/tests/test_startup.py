import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def startup():
    path = Path(__file__).parents[2] / 'scripts/station-vault.py'
    spec = importlib.util.spec_from_file_location('station_startup', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('state', ['ready','paused','disabled'])
def test_startup_respects_manual_lock_and_carelink_mode(startup, monkeypatch, state):
    calls = []
    monkeypatch.setattr(startup.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=state.encode()))
    monkeypatch.setattr(startup, 'operate', lambda *args, **kwargs: calls.append(args))
    assert startup.ensure_ready('192.168.8.57') == state
    assert not calls


@pytest.mark.parametrize('state', ['locked','stopped'])
def test_startup_recovers_after_boot_or_service_failure(startup, monkeypatch, state):
    calls = []
    monkeypatch.setattr(startup.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=state.encode()))
    monkeypatch.setattr(startup, 'operate', lambda *args, **kwargs: calls.append((args, kwargs)))
    assert startup.ensure_ready('192.168.8.57') == 'started'
    assert calls[0][0] == ('unlock', '192.168.8.57')
    assert calls[0][1]['automatic'] is True


def test_keys_are_stdin_only_and_ssh_identity_is_pinned(startup, monkeypatch, tmp_path):
    directory = tmp_path / '.local/security'
    directory.mkdir(parents=True)
    (directory / 'board-sudo-password').write_text('fixture-sudo')
    (directory / 'vault-password').write_text('fixture-vault')
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=b'ready')
    monkeypatch.setattr(startup.subprocess, 'run', run)
    startup.operate('unlock', '192.168.8.57', automatic=True, root=tmp_path)
    command, options = calls[0]
    assert 'fixture-' not in ' '.join(command)
    assert options['input'] == b'fixture-sudo\nfixture-vault'
    assert 'StrictHostKeyChecking=yes' in command and 'BatchMode=yes' in command
    assert command[-1].endswith('unlock-vault.sh --automatic')
    with pytest.raises(ValueError):
        startup.ssh_command('-oProxyCommand=unsafe')
