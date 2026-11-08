import asyncio
import threading

END_OF_REPLY = object()


class LiveReplyStream:
    """Runs one ConversationSession reply on a worker thread and hands its text to the voice agent's event loop as it
    streams. When the agent stops reading (the user interrupted), the LLM stream is abandoned at its next chunk."""

    def __init__(self, conversation_session, user_message, on_passages):
        self.conversation_session = conversation_session
        self.user_message = user_message
        self.on_passages = on_passages
        self.stop_requested = threading.Event()

    async def stream_text(self):
        event_loop = asyncio.get_running_loop()
        text_queue = asyncio.Queue()
        reply_thread = threading.Thread(target=self.run_reply, args=(event_loop, text_queue), daemon=True)
        reply_thread.start()
        try:
            while True:
                queued_item = await text_queue.get()
                if queued_item is END_OF_REPLY:
                    return
                if isinstance(queued_item, Exception):
                    raise queued_item
                yield queued_item
        finally:
            self.stop_requested.set()

    def run_reply(self, event_loop, text_queue):
        def forward_text(text):
            if self.stop_requested.is_set():
                raise ReplyAbandoned()
            event_loop.call_soon_threadsafe(text_queue.put_nowait, text)

        def forward_passages(passages, retrieval_seconds):
            event_loop.call_soon_threadsafe(self.on_passages, passages, retrieval_seconds)

        try:
            self.conversation_session.reply(self.user_message, on_text=forward_text, on_passages=forward_passages)
            event_loop.call_soon_threadsafe(text_queue.put_nowait, END_OF_REPLY)
        except ReplyAbandoned:
            pass
        except Exception as reply_error:
            event_loop.call_soon_threadsafe(text_queue.put_nowait, reply_error)


class ReplyAbandoned(Exception):
    pass
