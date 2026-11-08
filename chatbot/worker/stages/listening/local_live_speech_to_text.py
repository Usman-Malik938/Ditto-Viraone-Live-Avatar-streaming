import asyncio

from livekit import rtc
from livekit.agents import stt
from livekit.agents.language import LanguageCode


class LocalLiveSpeechToText(stt.STT):
    """LiveKit speech-to-text backed by local faster-whisper, used when ElevenLabs is switched off.
    It is not streaming, so LiveKit cuts the audio into utterances with the VAD and sends each one here."""

    def __init__(self, recognizer, language_code, recognizer_lock):
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self.recognizer = recognizer
        self.language_code = language_code
        self.recognizer_lock = recognizer_lock

    async def _recognize_impl(self, buffer, *, language=None, conn_options=None):
        wav_bytes = rtc.combine_audio_frames(buffer).to_wav_bytes()
        transcription = await asyncio.to_thread(self.transcribe_locked, wav_bytes)
        speech = stt.SpeechData(language=LanguageCode(self.language_code), text=transcription.text)
        return stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[speech])

    def transcribe_locked(self, wav_bytes):
        """The local models share the CPU, so one clip at a time."""
        with self.recognizer_lock:
            return self.recognizer.transcribe(wav_bytes)
