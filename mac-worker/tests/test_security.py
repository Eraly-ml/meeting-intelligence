import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
from pathlib import Path
import socket
import ssl
import sys
import threading

import httpx
import pytest
from uvicorn.config import create_ssl_context

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'station' / 'src'))
from meeting_station.config import Settings as StationSettings
from meeting_station.worker import MacClient
from meeting_worker.config import Settings


def test_lan_encryption_cannot_silently_downgrade():
    with pytest.raises(ValueError, match='mutual TLS'):
        Settings(_env_file=None, bind_host='0.0.0.0').server_tls()
    with pytest.raises(ValueError, match='LAN worker requires HTTPS'):
        StationSettings(token='station-test-token', worker_token='worker-test-token', worker_url='http://192.168.8.84:8765')
    with pytest.raises(ValueError, match='all three'):
        StationSettings(token='station-test-token', worker_token='worker-test-token', worker_ca_file='ca.crt')


def test_mutual_tls_requires_trusted_client_and_correct_server_identity(tmp_path):
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'create-worker-pki.py'
    spec = importlib.util.spec_from_file_location('pki', script)
    pki = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pki)
    directory = tmp_path / 'pki'
    pki.provision(directory, '127.0.0.1')
    config = Settings(_env_file=None, bind_host='0.0.0.0', tls_cert_file=str(directory / 'worker.crt'),
                      tls_key_file=str(directory / 'worker.key'), tls_client_ca_file=str(directory / 'ca.crt'))
    options = config.server_tls()
    context = create_ssl_context(certfile=options['ssl_certfile'], keyfile=options['ssl_keyfile'],
        password=None, ssl_version=options['ssl_version'], cert_reqs=options['ssl_cert_reqs'],
        ca_certs=options['ssl_ca_certs'], ciphers=options['ssl_ciphers'])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = StationSettings(token='station-test-token', worker_token='worker-test-token',
        worker_url=f'https://127.0.0.1:{server.server_port}', worker_ca_file=str(directory / 'ca.crt'),
        worker_cert_file=str(directory / 'station.crt'), worker_key_file=str(directory / 'station.key'))
    try:
        async def accepted():
            client = MacClient(settings)
            try:
                assert await client.json('GET', '/health') == {'ok': True}
            finally:
                await client.close()
        asyncio.run(accepted())
        no_identity = ssl.create_default_context(cafile=str(directory / 'ca.crt'))
        with httpx.Client(verify=no_identity, trust_env=False) as client, pytest.raises(httpx.TransportError):
            client.get(settings.worker_url)
        with httpx.Client(trust_env=False) as client, pytest.raises(httpx.TransportError):
            client.get(settings.worker_url)
        with socket.create_connection(('127.0.0.1', server.server_port)) as raw, pytest.raises(ssl.SSLCertVerificationError):
            settings.worker_tls().wrap_socket(raw, server_hostname='192.168.8.99')
        # A server identity from the same CA cannot impersonate the station.
        wrong_purpose = replace(settings, worker_cert_file=str(directory / 'worker.crt'), worker_key_file=str(directory / 'worker.key'))
        with httpx.Client(verify=wrong_purpose.worker_tls(), trust_env=False) as client, pytest.raises(httpx.TransportError):
            client.get(settings.worker_url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
