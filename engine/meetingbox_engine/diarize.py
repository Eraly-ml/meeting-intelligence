"""Isolated sherpa subprocess: a stuck native inference cannot block the API."""
import argparse
import json
import wave
from pathlib import Path


def run(audio, segmentation, embedding, output):
    import numpy as np
    import sherpa_onnx

    with wave.open(audio, "rb") as source:
        if source.getframerate() != 16000 or source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("Diarization needs mono 16 kHz PCM16")
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=segmentation),
            num_threads=2, provider="cpu"),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=embedding, num_threads=2, provider="cpu"),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3, min_duration_off=0.5)
    if not config.validate():
        raise ValueError("Invalid local diarization models")
    pipeline = sherpa_onnx.OfflineSpeakerDiarization(config)
    turns = [{"start": float(item.start), "end": float(item.end), "speaker": "speaker_{:02d}".format(item.speaker)}
             for item in pipeline.process(samples).sort_by_start_time()]
    Path(output).write_text(json.dumps(turns), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("audio", "segmentation", "embedding", "output"):
        parser.add_argument("--" + name, required=True)
    run(**vars(parser.parse_args()))
