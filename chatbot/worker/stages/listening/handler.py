import threading

from worker import config
from worker.stages.listening.speech_client import NemotronSpeechToText
from worker.stages.listening.speech_server import NemotronSpeechServer


class ListeningStage:
    """Turns the user's audio into text and decides when they have finished speaking."""

    def __init__(self):
        self.speech_recognizers = {}
        self.recognizer_lock = threading.Lock()
        self.uses_elevenlabs = (
            config.SPEECH_TO_TEXT_PROVIDER == "elevenlabs" and config.ELEVENLABS_ENABLED and bool(config.ELEVENLABS_API_KEY)
        )

    def get_speech_recognizer(self, language):
        """The local fallback. English uses the faster English-only model and Polish a multilingual one."""
        if language.code not in self.speech_recognizers:
            from worker.stages.listening.speech_recognizer import FasterWhisperRecognizer
            self.speech_recognizers[language.code] = FasterWhisperRecognizer(language.speech_recognition_model, language.speech_recognition_language)
        return self.speech_recognizers[language.code]

    def warm_up(self, language):
        """Loads the local model ahead of the first call; Scribe has nothing to load."""
        if not self.uses_elevenlabs:
            self.get_speech_recognizer(language).warm_up()

    def describe_speech_recognizer(self, language):
        if self.uses_elevenlabs:
            return f"elevenlabs {config.ELEVENLABS_REALTIME_SPEECH_TO_TEXT_MODEL}"
        return f"local {language.speech_recognition_model}"

    def speech_recognizer_notice(self):
        """Says why the local model is used when ElevenLabs speech-to-text was asked for."""
        if config.SPEECH_TO_TEXT_PROVIDER != "elevenlabs" or self.uses_elevenlabs:
            return ""
        if not config.ELEVENLABS_ENABLED:
            return "Speech-to-text runs locally (ELEVENLABS_ENABLED=false)."
        return "Speech-to-text runs locally: add ELEVENLABS_API_KEY to .env."

    def build_voice_activity_detector(self):
        from livekit.plugins import silero
        return silero.VAD.load()

    def build_live_speech_to_text(self, language):
        """Streaming Scribe for the voice agent, or local faster-whisper when ElevenLabs is off."""
        if self.uses_elevenlabs:
            from livekit.plugins import elevenlabs
            return elevenlabs.STT(
                api_key=config.ELEVENLABS_API_KEY,
                model=config.ELEVENLABS_REALTIME_SPEECH_TO_TEXT_MODEL,
                language_code=language.speech_recognition_language,
                tag_audio_events=False,
            )
        from worker.stages.listening.local_live_speech_to_text import LocalLiveSpeechToText
        return LocalLiveSpeechToText(self.get_speech_recognizer(language), language.speech_recognition_language, self.recognizer_lock)

    def build_turn_handling(self, language):
        """When the user's turn ends and what counts as interrupting the agent. The turn detector model reads the
        transcript to judge whether the user has finished, not just whether they went quiet."""
        from livekit.plugins.turn_detector.multilingual import MultilingualModel
        return {
            "turn_detection": MultilingualModel(),
            "endpointing": {
                "min_delay": language.min_end_of_turn_delay_seconds,
                "max_delay": config.MAX_END_OF_TURN_DELAY_SECONDS,
            },
            "interruption": {
                "mode": config.INTERRUPTION_MODE,
                "min_duration": config.MIN_INTERRUPTION_SECONDS,
                "false_interruption_timeout": config.FALSE_INTERRUPTION_TIMEOUT_SECONDS,
                "resume_false_interruption": config.RESUME_AFTER_FALSE_INTERRUPTION,
            },
            "preemptive_generation": {"enabled": config.PREEMPTIVE_GENERATION_ENABLED},
        }

    def build_speech_to_text(self):
        return NemotronSpeechToText()

    def start_speech_server(self):
        NemotronSpeechServer().run()
