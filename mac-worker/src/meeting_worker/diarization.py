from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import sys
import tempfile

from .config import Settings
from .schemas import Transcript


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


def diarize(audio_path: Path, transcript: Transcript, config: Settings, min_speakers=None, max_speakers=None) -> Transcript:
    if config.diarization_backend == "sherpa-onnx":
        from .asr import local_audio_settings
        from .local_audio import AudioProcessor, command
        ready = AudioProcessor(local_audio_settings(config, True)).capabilities()["diarization"]["ready"]
        if not ready:
            raise RuntimeError("Sherpa diarization unavailable: install sherpa-onnx and local segmentation/embedding models")
        with tempfile.TemporaryDirectory(prefix="diarize-", dir=config.data_dir / "work") as directory:
            output = Path(directory) / "turns.json"
            arguments = [sys.executable, "-m", "meeting_worker.sherpa_process", "--audio", str(audio_path),
                     "--segmentation", config.segmentation_model, "--embedding", config.embedding_model,
                     "--output", str(output)]
            if min_speakers is not None and min_speakers == max_speakers:
                arguments.extend(["--num-speakers", str(min_speakers)])
            command(arguments, Path(directory), config.audio_timeout)
            turns = [SpeakerTurn(**item) for item in json.loads(output.read_text())]
            count = len({turn.speaker for turn in turns})
            if (min_speakers is not None and count < min_speakers) or (max_speakers is not None and count > max_speakers):
                raise RuntimeError("Detected speaker count is outside the requested bounds; revise bounds or review the recording")
        return assign_speakers(transcript, turns)
    if config.diarization_backend != "pyannote":
        raise RuntimeError("Unknown diarization backend")
    if not Path(config.diarization_model).is_dir():
        raise RuntimeError("Pyannote runtime requires a pre-provisioned local model directory")
    try:
        from pyannote.audio import Pipeline  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pyannote.audio is not installed") from exc

    pipeline = Pipeline.from_pretrained(
        config.diarization_model,
        use_auth_token=config.hf_token or None,
    )
    annotation = pipeline(str(audio_path), min_speakers=min_speakers, max_speakers=max_speakers)
    turns = [
        SpeakerTurn(float(turn.start), float(turn.end), str(speaker))
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]
    return assign_speakers(transcript, turns)


def assign_speakers(transcript: Transcript, turns: list[SpeakerTurn]) -> Transcript:
    """Attach the speaker with the largest overlap to every ASR segment."""
    for segment in transcript.segments:
        if segment.start is None or segment.end is None:
            continue
        from .local_audio import speaker_for
        label = speaker_for(segment.start, segment.end, [vars(turn) for turn in turns])
        segment.speaker = None if label == "unknown" else label
    return transcript
