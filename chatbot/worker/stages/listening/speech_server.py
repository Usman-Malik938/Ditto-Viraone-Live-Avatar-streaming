from worker import config


class NemotronSpeechServer:
    """Loads Nemotron once on the GPU and streams transcripts back for every connected conversation."""

    def __init__(self):
        self.model_id = config.SPEECH_MODEL_ID
        self.host = config.SPEECH_SERVER_HOST
        self.port = config.SPEECH_SERVER_PORT
        self.chunk_milliseconds = config.SPEECH_CHUNK_MILLISECONDS

    def run(self):
        raise NotImplementedError
