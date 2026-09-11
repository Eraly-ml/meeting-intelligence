from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .schemas import Transcript


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


def diarize(audio_path: Path, transcript: Transcript, config: Settings) -> Transcript:
    try:
        from pyannote.audio import Pipeline  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pyannote.audio is not installed") from exc

    pipeline = Pipeline.from_pretrained(
        config.diarization_model,
        use_auth_token=config.hf_token or None,
    )
    annotation = pipeline(str(audio_path))
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
        overlaps = [
            (max(0.0, min(segment.end, turn.end) - max(segment.start, turn.start)), turn)
            for turn in turns
        ]
        best_overlap, best = max(overlaps, default=(0.0, None), key=lambda pair: pair[0])
        if best is not None and best_overlap > 0:
            segment.speaker = best.speaker
    return transcript
