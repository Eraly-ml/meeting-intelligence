"""Explicit ALSA recording on the station, with full recordings kept on disk."""
import asyncio
import contextlib
import os
from pathlib import Path
import shutil
import signal
from uuid import uuid4

from .store import StoreError


def sync_file(path):
    with path.open("rb") as recording:
        os.fsync(recording.fileno())
    descriptor = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class BoardRecorder:
    def __init__(self, store, settings, publish):
        self.store, self.settings, self.publish = store, settings, publish
        self.lock = asyncio.Lock()
        self.active_id = None
        self.process = None
        self.watcher = None

    @property
    def available(self):
        # Installing arecord alone does not imply an input device exists.
        pcm = Path("/proc/asound/pcm")
        try:
            return bool(shutil.which("arecord") and pcm.exists() and "capture" in pcm.read_text())
        except OSError:
            return False

    async def start(self, title):
        async with self.lock:
            if self.active_id:
                raise StoreError("A board recording is already running")
            executable = shutil.which("arecord")
            if not executable or not self.available:
                raise StoreError("No ALSA microphone is available. Connect a USB microphone or upload an audio file", 503)
            meeting_id = str(uuid4())
            path = self.settings.archive / (meeting_id + ".wav")
            self.store.create_audio(meeting_id, title, path.name, "board-recording.wav", 0, recording=True)
            try:
                # A maximum file size prevents a forgotten recording from filling the board.
                process = await asyncio.create_subprocess_exec(
                    executable, "-q", "-D", self.settings.alsa_device, "-t", "wav", "-f", "S16_LE",
                    "-r", "16000", "-c", "1", "-d", str(max(1, (self.settings.max_upload_bytes - 4096) // 32000)),
                    str(path), stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError as exc:
                self.store.fail_audio({"meeting_id": meeting_id}, "Unable to start ALSA capture; check the station microphone device", 1)
                raise StoreError("Unable to start ALSA capture; check the station microphone device", 503) from exc
            self.active_id, self.process = meeting_id, process
            self.watcher = asyncio.create_task(self._watch(meeting_id, process))
            self.publish(meeting_id)
            return self.store.snapshot(meeting_id)

    async def _watch(self, meeting_id, process):
        await process.wait()
        async with self.lock:
            if self.active_id == meeting_id:
                self.active_id = self.process = self.watcher = None
                path = self.settings.archive / (meeting_id + ".wav")
                if process.returncode == 0 and path.exists() and path.stat().st_size > 44:
                    await asyncio.to_thread(sync_file, path)
                    self.store.queue_recording(meeting_id, path.stat().st_size)
                else:
                    self.store.fail_audio({"meeting_id": meeting_id}, "Microphone capture stopped unexpectedly; available audio is archived. Check the input device and retry transcription", 1)
                self.publish(meeting_id)

    async def stop(self, meeting_id):
        async with self.lock:
            if self.active_id != meeting_id or self.process is None:
                meeting = self.store.snapshot(meeting_id)
                if meeting["audio"] and meeting["audio"]["status"] != "recording":
                    return meeting  # Replayed stop is harmless.
                raise StoreError("This meeting is not the active board recording")
            process, watcher = self.process, self.watcher
            self.active_id = self.process = self.watcher = None
            if watcher:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(process.wait(), timeout=10)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            path = self.settings.archive / (meeting_id + ".wav")
            if not path.exists() or path.stat().st_size <= 44:
                self.store.fail_audio({"meeting_id": meeting_id}, "Microphone produced no audio; check the station input device", 1)
            else:
                await asyncio.to_thread(sync_file, path)
                self.store.queue_recording(meeting_id, path.stat().st_size)
            self.publish(meeting_id)
            return self.store.snapshot(meeting_id)

    async def close(self):
        if self.active_id:
            await self.stop(self.active_id)
