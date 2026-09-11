#!/usr/bin/env python3
"""Loopback control for one isolated Chromium and bounded PulseAudio capture."""
import array
import contextlib
import hmac
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def meeting_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError("Provide a valid HTTPS meeting link")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
        raise ValueError("Meeting links must use HTTPS without user credentials or fragments")
    host = (parsed.hostname or "").lower()
    if host == "meet.google.com" and re.fullmatch(r"/[a-z]{3}-[a-z]{4}-[a-z]{3}/?", parsed.path):
        return value, "meet"
    if (host == "zoom.us" or host.endswith(".zoom.us")) and re.match(r"/(j|wc|w)/[0-9]+(?:/|$)", parsed.path):
        return value, "zoom"
    if host in {"teams.microsoft.com", "teams.live.com"} and parsed.path.startswith(("/l/meetup-join/", "/meet/")):
        return value, "teams"
    raise ValueError("Use a Google Meet, Zoom or Microsoft Teams meeting link")


class ControllerError(Exception):
    def __init__(self, message, status=409):
        self.status = status
        super().__init__(message)


class Controller:
    def __init__(self, data_dir, max_bytes=512 * 1024 * 1024):
        self.directory = Path(data_dir) / "recordings"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_bytes = max_bytes
        self.lock = threading.RLock()
        self.active = None
        self.process = None
        self.platform = None
        self.opened = False
        self.page_id = None
        self.join_state = None
        self.audio_activity = {}
        self.join_script = Path(__file__).with_name('join.js').read_text() if Path(__file__).with_name('join.js').exists() else None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for path in self.directory.glob("*.json"):
            with contextlib.suppress(ValueError, OSError):
                record = json.loads(path.read_text())
                if record["state"] == "recording":
                    record.update(state="interrupted", error="Browser service restarted; any captured audio is retained")
                    self.save(record)

    def path(self, recording_id, extension):
        return self.directory / (str(uuid.UUID(recording_id)) + extension)

    def save(self, record):
        path = self.path(record["id"], ".json")
        temporary = path.with_suffix(".partial")
        with temporary.open("w") as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)

    def record(self, recording_id):
        try:
            record = json.loads(self.path(recording_id, ".json").read_text())
        except FileNotFoundError as exc:
            raise ControllerError("Browser recording not found", 404) from exc
        audio = self.path(recording_id, ".wav")
        record["bytes"] = audio.stat().st_size if audio.exists() else 0
        if recording_id == self.active:
            record["audio_health"] = self.audio_health(record)
        return record

    def audio_health(self, record):
        """Measure saved PCM, rather than inferring sound from a growing file."""
        now = time.time()
        activity = self.audio_activity.setdefault(record['id'], {
            'bytes': 0, 'changed_at': now, 'last_signal_at': None, 'rms_dbfs': -96.0,
        })
        if record['bytes'] != activity['bytes']:
            activity.update(bytes=record['bytes'], changed_at=now)
            try:
                with self.path(record['id'], '.wav').open('rb') as source:
                    with wave.open(source, 'rb') as stream:
                        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000):
                            raise wave.Error('Unexpected capture format')
                        # FFmpeg leaves the live WAV length open until it stops.
                        # The underlying file is positioned at the PCM data here.
                        frames = min(stream.getnframes(), max(0, (record['bytes'] - source.tell()) // 2))
                        stream.setpos(max(0, frames - 48000))
                        samples = array.array('h', stream.readframes(min(frames, 48000)))
                if sys.byteorder != 'little':
                    samples.byteswap()
                rms = math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))
                activity['rms_dbfs'] = round(max(-96.0, 20 * math.log10(max(rms, .5) / 32768)), 1)
                if rms > 8:
                    activity['last_signal_at'] = now
            except (OSError, EOFError, wave.Error):
                activity['rms_dbfs'] = -96.0
        last_signal = activity['last_signal_at']
        quiet_seconds = max(0, now - (last_signal or record.get('created_at', now)))
        stalled = now - activity['changed_at'] >= 15
        state = 'stalled' if stalled else 'receiving' if last_signal and quiet_seconds < 5 else 'quiet' if last_signal else 'waiting'
        return {'state': state, 'rms_dbfs': activity['rms_dbfs'] if state == 'receiving' else -96.0,
                'quiet_seconds': round(quiet_seconds), 'received': last_signal is not None}

    def cdp_json(self, path):
        with self.opener.open("http://127.0.0.1:9222" + path, timeout=3) as response:
            return json.loads(response.read(2 * 1024 * 1024))

    def status(self):
        with self.lock:
            try:
                self.cdp_json("/json/version")
                available = True
            except Exception:
                available = False
            records = []
            for path in sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
                with contextlib.suppress(ValueError, OSError, ControllerError):
                    records.append(self.record(path.stem))
            return {"available": available, "state": "opened" if self.opened and available else "ready" if available else "unavailable",
                    "platform": self.platform, "active_recording_id": self.active, "recordings": records,
                    "join": self.join_state,
                    "message": "Meeting browser ready. Paste a meeting link to join and record automatically.",
                    "viewer_path": "/v1/browser/view/vnc_lite.html"}

    def open(self, url):
        import websocket
        url, platform = meeting_url(url)
        with self.lock:
            if self.active:
                raise ControllerError("Stop the current recording before opening another meeting")
            try:
                pages = [item for item in self.cdp_json("/json/list") if item.get("type") == "page"]
                if not pages:
                    raise ControllerError("Chromium has no page ready; restart the browser service", 503)
                address = pages[0]["webSocketDebuggerUrl"]
                self.page_id = pages[0]['id']
                parsed = urllib.parse.urlsplit(address)
                if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != 9222:
                    raise ControllerError("Chromium returned an invalid local debugging address", 502)
                connection = websocket.create_connection(address, timeout=5, suppress_origin=True)
                try:
                    connection.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
                    for _ in range(40):
                        response = json.loads(connection.recv())
                        if response.get("id") == 1:
                            if response.get("error") or response.get("result", {}).get("errorText"):
                                raise ControllerError("Chromium could not open this meeting link", 502)
                            break
                    else:
                        raise ControllerError("Chromium did not confirm navigation", 504)
                finally:
                    connection.close()
            except ControllerError:
                raise
            except Exception as exc:
                raise ControllerError("Meeting browser is unavailable; check its service", 503) from exc
            self.platform, self.opened = platform, True
            self.join_state = None
            return self.status()

    def cdp(self, method, params):
        import websocket
        pages = [p for p in self.cdp_json('/json/list') if p.get('type') == 'page' and (self.page_id is None or p.get('id') == self.page_id)]
        if not pages:
            raise ControllerError('The meeting browser tab is unavailable', 503)
        address = pages[0]['webSocketDebuggerUrl']
        parsed = urllib.parse.urlsplit(address)
        if parsed.hostname not in {'127.0.0.1', 'localhost'} or parsed.port != 9222:
            raise ControllerError('Invalid local browser debugging address', 502)
        connection = websocket.create_connection(address, timeout=5, suppress_origin=True)
        try:
            connection.send(json.dumps({'id':1,'method':method,'params':params}))
            for _ in range(100):
                response = json.loads(connection.recv())
                if response.get('id') == 1:
                    if response.get('error'):
                        raise ControllerError('The browser could not perform the meeting action', 502)
                    return response.get('result', {})
            raise ControllerError('The browser did not confirm the meeting action', 504)
        finally:
            connection.close()

    def join(self, url, recording_id):
        recording_id = str(uuid.UUID(recording_id))
        with self.lock:
            if self.active == recording_id and self.join_state:
                return self.status()
            if not self.join_script:
                raise ControllerError('Automatic joining is not installed', 503)
            self.open(url)
            # Permission denial applies to all provider subframes and redirects.
            # The isolated input sink is also silent; real microphone audio is
            # never sent back into the meeting by this participant.
            for permission in ('microphone', 'camera'):
                self.cdp('Browser.setPermission', {'permission':{'name':permission}, 'setting':'denied'})
            self.join_state = {'recording_id':recording_id, 'state':'joining', 'admitted':False, 'message':'Preparing the meeting page.'}
            # Capture before the join request so admission cannot lose the first
            # words. A refused join is archived as a failed capture, not a report.
            self.start(recording_id)
            threading.Thread(target=self.automate, args=(recording_id,), daemon=True).start()
            return self.status()

    def observe_join(self):
        response = self.cdp('Runtime.evaluate', {'expression':self.join_script, 'returnByValue':True, 'userGesture':True, 'timeout':5000})
        result = response.get('result', {}).get('value')
        if response.get('exceptionDetails') or not isinstance(result, dict) or result.get('state') not in {'joining','waiting','joined','blocked','ended'}:
            raise ControllerError('The meeting page did not return a recognized join state', 502)
        return result

    def automate(self, recording_id):
        deadline = time.monotonic() + 300
        joined = False
        failures = 0
        while True:
            time.sleep(2)
            with self.lock:
                if self.active != recording_id:
                    return
                try:
                    state = self.observe_join()
                    failures = 0
                except Exception:
                    failures += 1
                    if failures < 5:
                        continue
                    state = {'state':'blocked','message':'The station lost access to the meeting page; captured audio is retained.'}
                if not joined and time.monotonic() > deadline:
                    state = {'state':'blocked','message':'The join request timed out without admission. Check the host and guest access.'}
                if state['state'] == 'joined':
                    joined = True
                self.join_state = dict(state, recording_id=recording_id, admitted=joined)
                if state['state'] in {'blocked','ended'}:
                    self.stop(recording_id)
                    if not joined or state['state'] == 'blocked':
                        record = self.record(recording_id)
                        record.update(state='interrupted', error=state['message'])
                        self.save(record)
                    return

    def start(self, recording_id):
        recording_id = str(uuid.UUID(recording_id))
        with self.lock:
            if self.active:
                if self.active == recording_id:
                    return self.record(recording_id)
                raise ControllerError("A browser recording is already active")
            if self.path(recording_id, ".json").exists():
                raise ControllerError("This browser recording already exists; import its retained audio")
            self.audio_activity.clear()
            record = {"id": recording_id, "state": "recording", "created_at": time.time(), "bytes": 0, "error": None}
            self.save(record)
            maximum_seconds = min(4 * 3600, max(1, (self.max_bytes - 4096) // 32000))
            try:
                self.process = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "pulse", "-i", "meeting_output.monitor", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-flush_packets", "1", "-t", str(maximum_seconds), "-fs", str(self.max_bytes), "-n", str(self.path(recording_id, ".wav"))], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                record.update(state="interrupted", error="Unable to start browser audio capture")
                self.save(record)
                raise ControllerError(record["error"], 503) from exc
            self.active = recording_id
            threading.Thread(target=self.watch, args=(recording_id, self.process), daemon=True).start()
            return record

    def watch(self, recording_id, process):
        process.wait()
        with self.lock:
            if self.active == recording_id:
                self.finalize(recording_id, process.returncode not in (0, 255))
                self.active = self.process = None
                if self.join_state and self.join_state['recording_id'] == recording_id:
                    self.join_state.update(state='ended', message='Capture finished. Saving the recording.')
                    with contextlib.suppress(Exception):
                        self.cdp('Page.navigate', {'url':'about:blank'})

    def finalize(self, recording_id, forced=False):
        record = self.record(recording_id)
        path = self.path(recording_id, ".wav")
        valid = False
        contains_audio = False
        try:
            with wave.open(str(path), "rb") as stream:
                valid = stream.getnframes() > 0 and stream.getnchannels() == 1 and stream.getframerate() == 16000
                if valid:
                    while chunk := stream.readframes(160000):
                        if chunk.strip(b'\x00'):
                            contains_audio = True
                            break
            if valid:
                with path.open("rb") as stream:
                    os.fsync(stream.fileno())
                path.chmod(0o600)
        except (OSError, EOFError, wave.Error):
            pass
        error = None
        if not valid or forced:
            error = "Capture stopped unexpectedly or produced no valid WAV; available audio is retained"
        elif not contains_audio:
            error = "No sound was captured. The station saved silence; check the meeting audio before recording again. The original file is retained."
        record.update(state="stopped" if error is None else "interrupted", stopped_at=time.time(),
                      audio_received=contains_audio, error=error)
        self.save(record)
        return record

    def stop(self, recording_id):
        with self.lock:
            record = self.record(recording_id)
            if self.active != recording_id:
                return record
            process = self.process
            forced = False
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    forced = True
                    process.kill()
                    process.wait(timeout=5)
            forced = forced or process.returncode not in (0, 255)
            self.active = self.process = None
            result = self.finalize(recording_id, forced)
            if self.join_state and self.join_state['recording_id'] == recording_id:
                if not self.join_state.get('admitted'):
                    result.update(state='interrupted', error='The station did not confirm admission into the meeting. Captured waiting-room audio is retained.')
                    self.save(result)
                if self.join_state['state'] not in {'blocked','ended'}:
                    self.join_state.update(state='ended', message='Left the meeting. Saving and processing its recording.')
                with contextlib.suppress(Exception):
                    self.cdp('Page.navigate', {'url':'about:blank'})
            return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # Meeting URLs and credentials must not enter access logs.

    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self):
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.token):
            self.close_connection = True
            return self.reply(401, {"detail": "Browser controller authentication required"})
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return self.reply(400, {"detail": "Chunked controller requests are not accepted"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 16384:
                raise ControllerError("Controller request exceeds 16 KiB", 413)
            body = json.loads(self.rfile.read(length)) if length else {}
            path = urllib.parse.urlsplit(self.path).path
            controller = self.server.controller
            if self.command == "GET" and path in {"/health", "/v1/status"}:
                return self.reply(200, controller.status())
            if self.command == "POST" and path == "/v1/open":
                return self.reply(200, controller.open(body.get("url")))
            if self.command == 'POST' and path == '/v1/join':
                return self.reply(202, controller.join(body.get('url'), body.get('id')))
            if self.command == "POST" and path == "/v1/recordings/start":
                return self.reply(202, controller.start(body.get("id")))
            match = re.fullmatch(r"/v1/recordings/([0-9a-f-]{36})/(stop|audio)", path)
            if match and self.command == "POST" and match[2] == "stop":
                return self.reply(200, controller.stop(match[1]))
            if match and self.command == "GET" and match[2] == "audio":
                record = controller.record(match[1])
                if record["state"] == "recording":
                    raise ControllerError("Stop browser capture before importing audio")
                audio = controller.path(match[1], ".wav")
                if not audio.exists() or not record["bytes"]:
                    raise ControllerError("No browser audio is available for this recording", 404)
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(audio.stat().st_size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with audio.open("rb") as stream:
                    while chunk := stream.read(256 * 1024):
                        self.wfile.write(chunk)
                return
            raise ControllerError("Controller route not found", 404)
        except ControllerError as exc:
            self.reply(exc.status, {"detail": str(exc)})
        except (ValueError, TypeError, AttributeError):
            self.reply(422, {"detail": "Invalid controller request or meeting link"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.reply(500, {"detail": "Browser controller failed; retained recordings are preserved"})

    do_GET = do_POST = dispatch


def main():
    token = os.environ.get("MI_BROWSER_TOKEN", "")
    if len(token) < 16 or token != token.strip():
        raise SystemExit("MI_BROWSER_TOKEN must contain at least 16 characters")
    server = ThreadingHTTPServer(("127.0.0.1", 8770), Handler)
    server.token = token
    server.controller = Controller(os.environ.get("MI_BROWSER_DATA_DIR", "/var/lib/meeting-browser"))
    server.serve_forever()


if __name__ == "__main__":
    main()
