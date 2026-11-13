import asyncio
import logging

from worker import config

logger = logging.getLogger(__name__)


class CallLimits:
    """The session safeguards inside one call (PoC agreement, section 3): the call ends after SESSION_MAX_SECONDS, or
    after SESSION_INACTIVITY_SECONDS in which neither the visitor nor the avatar speaks. LiveKit's own away timer
    (AgentSession user_away_timeout) measures the silence. Ending a call: tell the page why, say the profile's goodbye,
    then close the room, which stops speech-to-text, the language model, ElevenLabs and the avatar for this call."""

    def __init__(self, job_context, session, language_code):
        self.job_context = job_context
        self.session = session
        self.language_code = language_code
        self.loop = None
        self.time_limit_timer = None
        self.ending_task = None

    def start(self):
        """Call once the session has started: the time limit counts from here."""
        self.loop = asyncio.get_running_loop()
        self.session.on("user_state_changed", self.handle_user_state_change)
        if config.SESSION_MAX_SECONDS > 0:
            self.time_limit_timer = self.loop.call_later(config.SESSION_MAX_SECONDS, self.end_call, "time_limit")
        self.job_context.add_shutdown_callback(self.stop_timer)

    def handle_user_state_change(self, state_change):
        if state_change.new_state == "away" and config.SESSION_INACTIVITY_SECONDS > 0:
            self.end_call("inactivity")

    def end_call(self, reason):
        """Safe to call more than once; only the first reason counts. Must run on the call's event loop."""
        if self.ending_task is not None:
            return
        logger.info("ending the call: %s", reason)
        self.ending_task = asyncio.create_task(self.say_goodbye_and_close(reason))

    async def say_goodbye_and_close(self, reason):
        await self.tell_page(reason)
        if reason in config.SESSION_MESSAGE_KINDS:
            await self.say_goodbye(config.CLIENT_PROFILE.session_message(reason, self.language_code))
        # Deleting the room disconnects the visitor too, so their microphone stops streaming.
        await self.job_context.delete_room()
        self.job_context.shutdown(reason=f"session safeguard: {reason}")

    async def tell_page(self, reason):
        try:
            await self.job_context.room.local_participant.set_attributes({config.CALL_END_ATTRIBUTE: reason})
        except Exception as attribute_error:
            logger.warning("could not tell the page why the call ends: %s", attribute_error)

    async def say_goodbye(self, goodbye):
        """Cuts off any reply in progress first; a goodbye that can't play within the limit is skipped."""
        try:
            await asyncio.wait_for(self.session.interrupt(force=True), timeout=2)
            goodbye_speech = self.session.say(goodbye, allow_interruptions=False, add_to_chat_ctx=False)
            await asyncio.wait_for(goodbye_speech.wait_for_playout(), timeout=config.SESSION_GOODBYE_MAX_SECONDS)
        except Exception as goodbye_error:
            logger.warning("the goodbye did not finish playing: %r", goodbye_error)

    async def stop_timer(self):
        if self.time_limit_timer is not None:
            self.time_limit_timer.cancel()

    def end_call_from_another_thread(self, reason):
        """The web server runs on its own thread; it ends a visitor's earlier call when they start a new one."""
        self.loop.call_soon_threadsafe(self.end_call, reason)
