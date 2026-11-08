"""Runs the LiveKit voice agent and wires the stages in order: listening, reasoning, speaking, avatar."""
import asyncio
import threading

# LiveKit plugins must be imported on the main thread before any call starts; the stages import them again lazily.
from livekit.agents import AgentServer, AgentSession, JobExecutorType, cli, room_io
from livekit.plugins import elevenlabs, openai, silero, turn_detector  # noqa: F401

from worker import config
from worker.livekit.knowledge_voice_agent import KnowledgeVoiceAgent
from worker.livekit.reply_details_publisher import ReplyDetailsPublisher
from worker.stages.listening.handler import ListeningStage
from worker.stages.avatar.handler import AvatarStage
from worker.stages.reasoning.handler import ReasoningStage
from worker.stages.speaking.handler import SpeakingStage


class VoiceAgentWorker:
    """Calls run as threads of this one process, so the embedding model, knowledge base and VAD load once and are
    shared. Separate processes would each load them, and embedded Qdrant allows only one process on its folder.
    Pass the test console's stages and conversation registry to share them with typed chat in the same process."""

    def __init__(self, listening_stage=None, reasoning_stage=None, speaking_stage=None, conversation_registry=None,
                 avatar_stage=None):
        self.listening_stage = listening_stage or ListeningStage()
        self.reasoning_stage = reasoning_stage or ReasoningStage()
        self.speaking_stage = speaking_stage or SpeakingStage()
        self.avatar_stage = avatar_stage or AvatarStage()
        self.conversation_registry = conversation_registry
        self.voice_activity_detector = None
        self.load_lock = threading.Lock()

    def run(self, on_registered=None):
        """Registers with LiveKit; takes LiveKit's own commands from sys.argv (console, dev, start, download-files).
        on_registered is called once the LiveKit server has accepted the agent."""
        agent_server = AgentServer(
            job_executor_type=JobExecutorType.THREAD,
            ws_url=config.LIVEKIT_URL,
            api_key=config.LIVEKIT_API_KEY,
            api_secret=config.LIVEKIT_API_SECRET,
        )
        agent_server.rtc_session(self.start_conversation)
        if on_registered is not None:
            agent_server.on("worker_registered", on_registered)
        cli.run_app(agent_server)

    async def start_conversation(self, job_context):
        await asyncio.to_thread(self.load_shared_models)
        await job_context.connect()
        call_settings = await self.read_call_settings(job_context)
        language = call_settings.language

        # Without a voice there is nothing for the face to say, so the avatar joins only when ElevenLabs speaks.
        avatar_call = None
        if self.speaking_stage.speech_enabled and not job_context.is_fake_job():
            avatar_call = await self.avatar_stage.join_call()
        text_to_speech = self.speaking_stage.build_live_text_to_speech(language, for_avatar=avatar_call is not None)
        session = AgentSession(
            vad=self.voice_activity_detector,
            stt=self.listening_stage.build_live_speech_to_text(language),
            llm=self.reasoning_stage.build_live_language_model(call_settings.model_choice),
            tts=text_to_speech,
            turn_handling=self.listening_stage.build_turn_handling(language),
        )
        agent = KnowledgeVoiceAgent(self.reasoning_stage, self.find_conversation(call_settings), call_settings.speak_greeting,
                                    ReplyDetailsPublisher(job_context.room), avatar_call)
        if avatar_call is not None:
            avatar_call.route_agent_voice(session)
            await avatar_call.start_publishing(job_context.room)
            job_context.add_shutdown_callback(avatar_call.close)

            # LiveKit keeps an empty room ~20 s before ending the call; end it at once so the next call gets the avatar.
            @job_context.room.on("participant_disconnected")
            def end_call_when_visitor_leaves(participant):
                if participant.identity.startswith(config.VOICE_VISITOR_IDENTITY_PREFIX):
                    job_context.shutdown(reason="the visitor left")
        await job_context.room.local_participant.set_attributes({config.AVATAR_STATE_ATTRIBUTE: self.describe_avatar(avatar_call)})
        # Without ElevenLabs there is no voice, so replies go out as text only; with the avatar, the avatar publishes
        # the voice together with the video. record=False keeps calls off LiveKit Cloud.
        room_options = room_io.RoomOptions(audio_output=text_to_speech is not None and avatar_call is None)
        await session.start(agent=agent, room=job_context.room, room_options=room_options, record=False)

        turn_bell = self.speaking_stage.build_turn_bell()
        if turn_bell is not None and not job_context.is_fake_job():
            await turn_bell.start(job_context.room, session)

    def describe_avatar(self, avatar_call):
        """Tells the page whether to wait for video or show the still portrait."""
        if avatar_call is not None:
            return "on"
        if self.avatar_stage.enabled:
            return "unavailable"
        return "off"

    def load_shared_models(self):
        """On the first call rather than at startup, so 'download-files' doesn't load the knowledge base."""
        with self.load_lock:
            if self.voice_activity_detector is not None:
                return
            for language in config.LANGUAGES.values():
                self.reasoning_stage.get_knowledge_lookup(language)
            self.voice_activity_detector = self.listening_stage.build_voice_activity_detector()

    async def read_call_settings(self, job_context):
        """The page sets these as participant attributes; console mode has no page, so it gets the defaults."""
        attributes = {}
        if not job_context.is_fake_job():
            participant = await job_context.wait_for_participant()
            attributes = participant.attributes
        return CallSettings(attributes)

    def find_conversation(self, call_settings):
        """The test console tab's own conversation when the call comes from there, otherwise a new one."""
        if self.conversation_registry is not None and call_settings.session_id:
            return self.conversation_registry.get_or_create(call_settings.session_id, call_settings.language,
                                                            call_settings.model_choice)
        return self.reasoning_stage.start_conversation(call_settings.language, call_settings.model_choice)


class CallSettings:
    """Language, model and conversation for one call, read from the joining page's participant attributes.
    Unknown or missing values fall back to the defaults rather than failing the call."""

    def __init__(self, attributes):
        language_code = attributes.get(config.VOICE_LANGUAGE_ATTRIBUTE) or config.DEFAULT_LANGUAGE_CODE
        if language_code not in config.LANGUAGES:
            language_code = config.DEFAULT_LANGUAGE_CODE
        self.language = config.LANGUAGES[language_code]
        self.model_choice = config.find_language_model_choice(attributes.get(config.VOICE_MODEL_ATTRIBUTE, ""))
        if self.model_choice is None:
            self.model_choice = config.DEFAULT_LANGUAGE_MODEL
        self.session_id = attributes.get(config.VOICE_SESSION_ATTRIBUTE, "")
        self.speak_greeting = attributes.get(config.VOICE_GREETING_ATTRIBUTE, "true") == "true"
