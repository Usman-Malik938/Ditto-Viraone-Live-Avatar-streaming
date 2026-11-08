import time

from livekit.agents import Agent, llm

from worker.livekit.reply_timeline import ReplyTimeline


class KnowledgeVoiceAgent(Agent):
    """The LiveKit agent for one call. LiveKit handles listening, turn-taking, interruptions and speech; the reply text
    comes from the reasoning stage, so voice and typed chat answer from the same knowledge base and prompt.
    The conversation session is shared with the test console tab when the call comes from there, so a call picks up
    the typed conversation and typing after the call picks up what was said."""

    def __init__(self, reasoning_stage, conversation_session, speak_greeting, reply_details_publisher, avatar_call=None):
        # The system prompt is built per reply by the conversation session, so LiveKit's instructions stay unused.
        super().__init__(instructions="", chat_ctx=self.build_chat_context(conversation_session.history))
        self.reasoning_stage = reasoning_stage
        self.conversation_session = conversation_session
        self.speak_greeting = speak_greeting
        self.reply_details_publisher = reply_details_publisher
        self.avatar_call = avatar_call
        self.reply_timeline = ReplyTimeline()
        self.user_speech_ended_at = None
        self.last_reply_delivered_at = time.perf_counter()

    def build_chat_context(self, history):
        chat_context = llm.ChatContext()
        for message in history:
            chat_context.add_message(role=message["role"], content=message["content"])
        return chat_context

    async def on_enter(self):
        self.session.on("user_state_changed", self.handle_user_state_change)
        self.session.on("close", self.handle_session_close)
        if self.speak_greeting:
            self.session.say(self.conversation_session.language.greeting)

    def handle_user_state_change(self, state_change):
        if state_change.old_state == "speaking" and state_change.new_state != "speaking":
            self.user_speech_ended_at = time.perf_counter()

    def handle_session_close(self, close_event):
        """The agent's own context, not the session's: only the agent's includes the typed turns it started with."""
        self.conversation_session.replace_history(self.heard_history(self.chat_ctx))

    def heard_history(self, chat_ctx):
        """User and agent messages as they were actually said; LiveKit cuts an interrupted reply to the words spoken."""
        history = []
        for message in chat_ctx.messages():
            if message.role in ("user", "assistant") and message.text_content:
                history.append({"role": message.role, "content": message.text_content})
        return history

    async def llm_node(self, chat_ctx, tools, model_settings):
        """chat_ctx ends with the user's new message; everything before it is what was said and heard so far."""
        history = self.heard_history(chat_ctx)
        if not history or history[-1]["role"] != "user":
            return
        user_message = history.pop()["content"]
        self.conversation_session.replace_history(history)
        speech_ended_at = None
        if self.user_speech_ended_at is not None and self.user_speech_ended_at > self.last_reply_delivered_at:
            speech_ended_at = self.user_speech_ended_at
        reply_timeline = ReplyTimeline(user_message, speech_ended_at)
        self.reply_timeline = reply_timeline
        reply_stream = self.reasoning_stage.stream_live_reply(self.conversation_session, history, user_message,
                                                              reply_timeline.note_passages)
        async for text in reply_stream:
            reply_timeline.note_text()
            yield text

    async def tts_node(self, text, model_settings):
        """LiveKit's own TTS step, wrapped only to time it against the LLM."""
        reply_timeline = self.reply_timeline
        if self.avatar_call is not None:
            self.avatar_call.report_first_face_frame_to(reply_timeline.note_face_frame)
        text_input = TrackedTextInput(text)
        async for audio_frame in Agent.default.tts_node(self, text_input.stream(), model_settings):
            reply_timeline.note_audio(text_input.still_arriving)
            yield audio_frame
        reply_timeline.log_streaming_summary()

    async def transcription_node(self, text, model_settings):
        """The reply text as the user receives it. The reply's details go to the page once the reply has finished
        playing (or was cut off): the text is done well before the voice, and with the avatar before the face."""
        reply_timeline = self.reply_timeline
        speech_handle = self.session.current_speech
        finished = False
        try:
            async for text_piece in Agent.default.transcription_node(self, text, model_settings):
                yield text_piece
            finished = True
        finally:
            if reply_timeline.has_reply_text():
                self.publish_details_after_playout(speech_handle, reply_timeline, finished)

    def publish_details_after_playout(self, speech_handle, reply_timeline, text_finished):
        def publish(finished_handle=None):
            self.last_reply_delivered_at = time.perf_counter()
            interrupted = not text_finished
            if finished_handle is not None:
                interrupted = finished_handle.interrupted
            self.reply_details_publisher.publish(reply_timeline.build_details(interrupted=interrupted))

        if speech_handle is None:
            publish()
            return
        speech_handle.add_done_callback(publish)


class TrackedTextInput:
    """Passes the reply text through to the TTS unchanged and records whether more text is still coming."""

    def __init__(self, text):
        self.text = text
        self.still_arriving = True

    async def stream(self):
        async for chunk in self.text:
            yield chunk
        self.still_arriving = False
