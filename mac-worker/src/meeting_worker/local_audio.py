import importlib.util
import json
import math
import os
import subprocess
import sys
import wave
from pathlib import Path

def local_file(path):
    return bool(path) and Path(path).is_file()


def executable(path):
    return local_file(path) and os.access(path, os.X_OK)


class EngineUnavailable(Exception):
    pass


class AudioError(Exception):
    pass


def command(arguments, directory, timeout):
    # Arguments are never passed through a shell. Logs stay in the temporary job.
    with (directory / "process.log").open("ab") as log:
        try:
            subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                           timeout=timeout, check=True, env=dict(os.environ, HF_HUB_OFFLINE="1"))
        except subprocess.TimeoutExpired as exc:
            raise AudioError("Local audio processing timed out; the station recording is preserved") from exc
        except (subprocess.CalledProcessError, OSError) as exc:
            raise AudioError("Local audio tool failed; verify its executable and model configuration") from exc


def speaker_for(start, end, turns):
    overlaps = {}
    for turn in turns:
        overlap = max(0.0, min(end, turn["end"]) - max(start, turn["start"]))
        overlaps[turn["speaker"]] = overlaps.get(turn["speaker"], 0) + overlap
    ranked = sorted(overlaps.items(), key=lambda item: (-item[1], item[0]))
    if not ranked or ranked[0][1] <= 0:
        return "unknown"
    # Do not confidently assign a long ASR segment spanning multiple voices.
    if len(ranked) > 1 and ranked[1][1] >= ranked[0][1] * 0.5:
        return "unknown"
    return ranked[0][0]


def parse_whisper(payload, duration, turns):
    if not isinstance(payload, dict) or not isinstance(payload.get("transcription"), list):
        raise AudioError("Whisper did not return timestamped transcription JSON")
    result = []
    for item in payload["transcription"]:
        text = item.get("text", "").strip()
        if not text or (text.startswith("[_") and text.endswith("_]")):
            continue
        offsets = item.get("offsets", {})
        try:
            start, end = float(offsets["from"]) / 1000, float(offsets["to"]) / 1000
        except (KeyError, TypeError, ValueError) as exc:
            raise AudioError("Whisper returned invalid timestamps") from exc
        if not all(math.isfinite(value) for value in (start, end)) or start < 0 or end < start or start > duration + 1:
            raise AudioError("Whisper returned out-of-range timestamps")
        end = min(end, duration)
        start = min(start, end)
        if len(text) > 8000:
            raise AudioError("Whisper segment exceeds the station transcript limit")
        tokens = []
        for token in item.get("tokens", []):
            fragment, probability = token.get("text", ""), token.get("p")
            if not fragment or fragment.startswith("[_") or fragment.startswith("<|"):
                continue
            if not isinstance(probability, (float, int)) or not math.isfinite(probability) or not 0 <= probability <= 1:
                raise AudioError("Whisper returned invalid token probabilities")
            tokens.append({"text": fragment, "probability": probability})
        # A review heuristic, not a calibrated probability of correctness.
        uncertain = any(token["probability"] < 0.6 and any(c.isalnum() for c in token["text"]) for token in tokens)
        result.append({"sequence": len(result) + 1, "start": start, "end": end,
                       "speaker": speaker_for(start, end, turns), "text": text,
                       "tokens": tokens, "needs_review": uncertain})
    return result


class AudioProcessor:
    def __init__(self, settings):
        self.settings = settings

    def capabilities(self):
        s = self.settings
        missing = [name for name, ready in (("whisper_binary", executable(s.whisper_binary)),
                   ("whisper_model", local_file(s.whisper_model)), ("ffmpeg_binary", executable(s.ffmpeg_binary))) if not ready]
        if getattr(s, "whisper_vad_model", "") and not local_file(s.whisper_vad_model):
            missing.append("whisper_vad_model")
        diarization_ready = (local_file(s.segmentation_model) and local_file(s.embedding_model)
                             and importlib.util.find_spec("sherpa_onnx") is not None
                             and importlib.util.find_spec("numpy") is not None)
        return {"transcription": {"ready": not missing, "missing": missing},
                "diarization": {"ready": bool(diarization_ready), "backend": "sherpa-onnx",
                                "status": "ready" if diarization_ready else "missing local models or sherpa-onnx"}}

    def transcribe(self, source, language):
        s = self.settings
        caps = self.capabilities()
        if not caps["transcription"]["ready"]:
            raise EngineUnavailable("ASR unavailable: configure " + ", ".join(caps["transcription"]["missing"]))
        directory = source.parent
        wav = directory / "normalized.wav"
        # Restrict decoder protocols to files: uploaded playlists cannot fetch URLs.
        command([s.ffmpeg_binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-protocol_whitelist", "file,pipe", "-format_whitelist", "wav,mp3,mov,matroska,webm,ogg,caf,flac",
                 "-i", str(source), "-map", "0:a:0", "-vn",
                 "-t", str(s.max_audio_seconds + 1), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                directory, min(300, s.process_timeout))
        try:
            with wave.open(str(wav), "rb") as stream:
                duration = stream.getnframes() / stream.getframerate()
        except (wave.Error, OSError, ZeroDivisionError) as exc:
            raise AudioError("Cannot decode this recording") from exc
        if duration <= 0 or duration > s.max_audio_seconds:
            raise AudioError("Recording is empty or exceeds the configured duration limit")
        output = directory / "whisper"
        arguments = [s.whisper_binary, "-m", s.whisper_model, "-f", str(wav), "-ojf", "-of", str(output),
                     "-l", language, "-t", "4", "-np", "-ml", "80", "-sow", "-bs", "5", "-mc", "0"]
        # Do not feed decoded text back into subsequent audio windows: an ASR
        # mistake can otherwise condition later windows into a repetition loop.
        if getattr(s, "whisper_prompt", "").strip():
            arguments += ["--prompt", s.whisper_prompt.strip(), "--carry-initial-prompt"]
        if getattr(s, "whisper_vad_model", ""):
            if not local_file(s.whisper_vad_model):
                raise EngineUnavailable("The configured local voice activity model is missing")
            arguments += ["--vad", "--vad-model", s.whisper_vad_model]
        command(arguments, directory, s.process_timeout)
        turns = []
        enabled = caps["diarization"]["ready"]
        if enabled:
            diarization_file = directory / "diarization.json"
            command([sys.executable, "-m", "meeting_worker.sherpa_process", "--audio", str(wav),
                     "--segmentation", s.segmentation_model, "--embedding", s.embedding_model,
                     "--output", str(diarization_file)], directory, s.process_timeout)
            try:
                turns = json.loads(diarization_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise AudioError("Diarization did not return valid output") from exc
        try:
            payload = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AudioError("Whisper did not return valid JSON") from exc
        detected_language = payload.get("result", {}).get("language", language)
        return {"segments": parse_whisper(payload, duration, turns), "duration": duration, "language": detected_language,
                "diarization": {"enabled": enabled, "backend": "sherpa-onnx" if enabled else None,
                                "status": "completed" if enabled else caps["diarization"]["status"]}}
