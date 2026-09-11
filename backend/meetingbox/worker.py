import asyncio
import logging

from .inference import InferenceError

logger = logging.getLogger("meetingbox.worker")


class Worker:
    def __init__(self, store, reasoner, settings, publish):
        self.store = store
        self.reasoner = reasoner
        self.settings = settings
        self.publish = publish

    async def run_once(self):
        self.store.schedule_due()
        job = self.store.claim()
        if job is None:
            return False
        try:
            meeting = self.store.snapshot(job["meeting_id"])
            report = await self.reasoner.reconcile(meeting, job["through_sequence"], job["kind"] == "final")
            self.store.complete(job, report)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Error strings must not contain transcript text or model output.
            message = str(exc) if isinstance(exc, InferenceError) else "Report processing failed ({}); transcript is preserved".format(type(exc).__name__)
            logger.warning("Report job %s failed: %s", job["id"], message)
            self.store.fail(job, message, self.settings.max_attempts)
        self.publish(job["meeting_id"])
        return True

    async def run(self):
        while True:
            if not await self.run_once():
                await asyncio.sleep(self.settings.poll_seconds)
