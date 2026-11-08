import time


class TextTurn:
    """One typed message outside a voice call: knowledge search and the streamed LLM reply, as text. During a call the
    page sends typed messages to the voice agent instead, so they are answered (and spoken) in the call."""

    def __init__(self, conversation_session, event_writer):
        self.conversation_session = conversation_session
        self.event_writer = event_writer
        self.started_at = time.perf_counter()

    def run(self, message):
        result = self.conversation_session.reply(message, on_text=self.event_writer.write_text,
                                                 on_passages=self.event_writer.write_passages)
        self.event_writer.write_done({
            "retrieval_seconds": result.retrieval_seconds,
            "first_token_seconds": result.first_token_seconds,
            "total_seconds": time.perf_counter() - self.started_at,
        })
