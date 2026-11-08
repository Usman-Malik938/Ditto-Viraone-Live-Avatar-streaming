import math

import numpy
from livekit import rtc
from livekit.agents import BackgroundAudioPlayer

from worker import config


class TurnBell:
    """Rings when the turn passes: a higher tone when the agent hands over to the user, a lower one when it takes the
    turn. Not when the user interrupts: they are already talking. Plays on its own track, so it never delays speech.
    LiveKit's console mode has no room to publish to, so the bell is silent there."""

    def __init__(self):
        self.player = BackgroundAudioPlayer()
        self.session = None
        self.user_turn_tone = self.build_tone(config.TURN_BELL_USER_TURN_HERTZ)
        self.agent_turn_tone = self.build_tone(config.TURN_BELL_AGENT_TURN_HERTZ)

    async def start(self, room, session):
        self.session = session
        await self.player.start(room=room, agent_session=session)
        session.on("agent_state_changed", self.handle_agent_state_change)

    def handle_agent_state_change(self, state_change):
        if state_change.new_state == "thinking" and state_change.old_state == "listening":
            self.ring(self.agent_turn_tone)
        elif state_change.new_state == "listening" and state_change.old_state in ("speaking", "thinking"):
            if self.session.user_state != "speaking":
                self.ring(self.user_turn_tone)

    def ring(self, tone):
        self.player.play(self.stream_tone(tone))

    async def stream_tone(self, tone):
        yield tone

    def build_tone(self, frequency_hertz):
        """A sine tone with a soft attack and a decaying tail, so it sounds like a bell rather than a beep."""
        sample_count = int(config.TURN_BELL_SECONDS * config.TURN_BELL_SAMPLE_RATE)
        sample_times = numpy.arange(sample_count) / config.TURN_BELL_SAMPLE_RATE
        wave = numpy.sin(2 * math.pi * frequency_hertz * sample_times)
        attack_samples = int(config.TURN_BELL_ATTACK_SECONDS * config.TURN_BELL_SAMPLE_RATE)
        envelope = numpy.exp(-sample_times * config.TURN_BELL_DECAY_PER_SECOND)
        envelope[:attack_samples] *= numpy.linspace(0.0, 1.0, attack_samples)
        samples = (wave * envelope * config.TURN_BELL_VOLUME * 32767).astype(numpy.int16)
        return rtc.AudioFrame(samples.tobytes(), config.TURN_BELL_SAMPLE_RATE, 1, sample_count)
