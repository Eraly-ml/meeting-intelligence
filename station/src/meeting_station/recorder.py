import asyncio
import contextlib
import shutil
import signal
from pathlib import Path
from uuid import uuid4

from .models import Manifest
from .store import StoreError


class Recorder:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.lock = asyncio.Lock()
        self.active_id = self.process = self.watcher = None

    @property
    def available(self):
        try:
            return bool(shutil.which("arecord") and "capture" in Path("/proc/asound/pcm").read_text())
        except OSError:
            return False

    async def start(self, body):
        async with self.lock:
            if self.active_id:
                raise StoreError("A board recording is already active")
            if not self.available:
                raise StoreError("No ALSA microphone is available. Connect a USB microphone or upload a recording", 503)
            job_id = str(uuid4())
            manifest = Manifest(meeting_id=job_id, **body.model_dump()).model_dump(mode="json")
            self.store.create(job_id, manifest, "audio", "board-recording.wav", job_id + ".wav", 0, "", recording=True)
            try:
                process = await asyncio.create_subprocess_exec(
                    shutil.which("arecord"), "-q", "-D", self.settings.alsa_device,
                    "-t", "wav", "-f", "S16_LE", "-r", "16000", "-c", "1",
                    "-d", str(max(1, (self.settings.max_upload_bytes - 4096) // 32000)),
                    str(self.store.source(job_id)), stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError as exc:
                self.store.finish_recording(job_id, "Unable to start ALSA capture; check the microphone device")
                raise StoreError("Unable to start ALSA capture; check the microphone device", 503) from exc
            self.active_id, self.process = job_id, process
            self.watcher = asyncio.create_task(self._watch(job_id, process))
            return self.store.get(job_id)

    async def _watch(self, job_id, process):
        await process.wait()
        async with self.lock:
            if self.active_id == job_id:
                self.active_id = self.process = self.watcher = None
                path = self.store.source(job_id)
                valid = process.returncode == 0 and path.exists() and path.stat().st_size > 44
                await asyncio.to_thread(self.store.finish_recording, job_id, None if valid else "Microphone capture stopped unexpectedly; available audio is archived. Check the input device")

    async def stop(self, job_id):
        async with self.lock:
            if self.active_id != job_id:
                job = self.store.get(job_id)
                if job["stage"] != "recording":
                    return job
                raise StoreError("This is not the active board recording")
            process, watcher = self.process, self.watcher
            self.active_id = self.process = self.watcher = None
            if watcher:
                watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watcher
            forced = False
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.send_signal(signal.SIGINT)
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except asyncio.TimeoutError:
                    forced = True
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            path = self.store.source(job_id)
            valid = path.exists() and path.stat().st_size > 44
            error = None if valid else "Microphone produced no audio; check the input device"
            if forced:
                error = "Microphone did not stop cleanly; partial audio is archived and may need repair. Retry transcription to check the available recording"
            return await asyncio.to_thread(self.store.finish_recording, job_id, error)

    async def close(self):
        if self.active_id:
            await self.stop(self.active_id)
