from worker import config


class NemotronSpeechToText:
    """LiveKit speech-to-text connector: streams call audio to the speech server and yields transcripts."""

    def __init__(self):
        self.server_address = f"ws://{config.SPEECH_SERVER_HOST}:{config.SPEECH_SERVER_PORT}"
        self.sample_rate = config.SPEECH_SAMPLE_RATE
