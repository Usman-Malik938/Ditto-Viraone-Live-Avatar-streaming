import io
import time

import av
import numpy
from faster_whisper import WhisperModel

from worker import config

WHISPER_SAMPLE_RATE = 16000


class TranscriptionResult:
    def __init__(self, text, audio_seconds, recognition_seconds):
        self.text = text
        self.audio_seconds = audio_seconds
        self.recognition_seconds = recognition_seconds


class RecordingDecoder:
    """Decodes any recording PyAV reads (browser webm/opus, wav) to 16 kHz mono float32.
    Done here rather than inside faster-whisper, whose decoder passes an option newer PyAV releases removed."""

    def decode(self, audio_bytes):
        resampler = av.AudioResampler(format="s16", layout="mono", rate=WHISPER_SAMPLE_RATE)
        sample_blocks = []
        with av.open(io.BytesIO(audio_bytes), mode="r") as container:
            for frame in container.decode(audio=0):
                for resampled_frame in resampler.resample(frame):
                    sample_blocks.append(resampled_frame.to_ndarray().reshape(-1))
            for resampled_frame in resampler.resample(None):
                sample_blocks.append(resampled_frame.to_ndarray().reshape(-1))
        if not sample_blocks:
            return numpy.zeros(0, dtype=numpy.float32)
        return numpy.concatenate(sample_blocks).astype(numpy.float32) / 32768.0


class FasterWhisperRecognizer:
    """Transcribes one recorded clip. Load once and share: the model takes seconds to load."""

    def __init__(self, model_name, language_code):
        self.decoder = RecordingDecoder()
        self.language_code = language_code
        self.model = WhisperModel(
            model_name,
            device=config.SPEECH_RECOGNITION_DEVICE,
            compute_type=config.SPEECH_RECOGNITION_COMPUTE_TYPE,
            download_root=str(config.MODEL_CACHE_DIRECTORY),
        )

    def warm_up(self):
        """The first real transcription is ~2.5x slower than later ones; run one on silence at startup instead."""
        segments, _ = self.model.transcribe(numpy.zeros(WHISPER_SAMPLE_RATE, dtype=numpy.float32), language=self.language_code, beam_size=1)
        for _ in segments:
            pass

    def transcribe(self, audio_bytes):
        started_at = time.perf_counter()
        waveform = self.decoder.decode(audio_bytes)
        audio_seconds = len(waveform) / WHISPER_SAMPLE_RATE
        if audio_seconds == 0:
            return TranscriptionResult("", 0.0, time.perf_counter() - started_at)
        segments, _ = self.model.transcribe(
            waveform,
            language=self.language_code,
            beam_size=config.SPEECH_RECOGNITION_BEAM_SIZE,
            vad_filter=True,
            hotwords=config.SPEECH_RECOGNITION_HOTWORDS,
            condition_on_previous_text=False,
        )
        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())
        return TranscriptionResult(" ".join(text_parts).strip(), audio_seconds, time.perf_counter() - started_at)
