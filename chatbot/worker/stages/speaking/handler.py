from worker import config


class SpeakingStage:
    """Turns the reply text into ElevenLabs speech for the voice agent, and rings the turn bell."""

    def __init__(self):
        self.speech_enabled = (
            config.ELEVENLABS_ENABLED and bool(config.ELEVENLABS_API_KEY) and config.ELEVENLABS_TEXT_TO_SPEECH_ENABLED
        )

    def speech_disabled_reason(self):
        if not config.ELEVENLABS_ENABLED:
            return "Voice is off (ELEVENLABS_ENABLED=false)."
        if not config.ELEVENLABS_API_KEY:
            return "Voice is off: add ELEVENLABS_API_KEY to .env."
        if not config.ELEVENLABS_TEXT_TO_SPEECH_ENABLED:
            return "Voice is off (ELEVENLABS_TEXT_TO_SPEECH_ENABLED=false)."
        return ""

    def build_live_text_to_speech(self, language, for_avatar=False, voice_choice=None):
        """ElevenLabs over a WebSocket: the LLM's words are fed in as they arrive. None when speech is off.
        voice_choice: the voice picked on the page; None = the language's own voice.
        For the avatar, ElevenLabs sends raw 16 kHz audio, the exact format Ditto hears, so nothing is converted;
        otherwise the plugin's default (compressed, quicker to first audio)."""
        if not self.speech_enabled:
            return None
        from livekit.plugins import elevenlabs
        options = {
            "api_key": config.ELEVENLABS_API_KEY,
            "voice_id": self.voice_id_for(language, voice_choice),
            "model": config.ELEVENLABS_MODEL,
            "language": language.elevenlabs_language_code,
        }
        if for_avatar:
            options["encoding"] = config.AVATAR_ELEVENLABS_OUTPUT_FORMAT
        return elevenlabs.TTS(**options)

    def voice_id_for(self, language, voice_choice):
        if voice_choice is not None:
            return voice_choice.voice_id
        return language.elevenlabs_voice_id

    def switch_voice(self, text_to_speech, language, voice_choice):
        """Mid-call voice change: the plugin opens a new connection, so the next sentence uses the new voice."""
        text_to_speech.update_options(voice_id=self.voice_id_for(language, voice_choice))

    def build_turn_bell(self):
        """None when TURN_BELL_ENABLED=false."""
        if not config.TURN_BELL_ENABLED:
            return None
        from worker.stages.speaking.turn_bell import TurnBell
        return TurnBell()
