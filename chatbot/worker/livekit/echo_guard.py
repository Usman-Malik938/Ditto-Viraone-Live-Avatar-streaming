"""Tells the avatar's own voice apart from the visitor's.

On a phone's built-in speaker the microphone also hears the avatar. The speech recognizer then turns the avatar's own
words into "visitor speech", which stopped answers mid-sentence and made the avatar reply to itself (day-6 testing).
While the avatar is speaking, whatever the recognizer hears is compared with what the avatar has just said: mostly the
same words means echo, which is ignored; different words mean the visitor really spoke, and the avatar stops to listen.
"""
import re
import time
import unicodedata

# Speech heard this long after the avatar stopped can still be the tail of its own voice.
ECHO_TAIL_SECONDS = 1.5
# How far back the avatar's own words are remembered.
SPOKEN_MEMORY_SECONDS = 25.0
# Share of the heard words that must come from the avatar's recent speech to count as echo.
ECHO_OVERLAP = 0.6


def words_of(text):
    """Lower-case words without accents or punctuation, so "Eskard," and "eskard" compare equal."""
    plain = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    plain = "".join(character for character in plain if not unicodedata.combining(character))
    return [word for word in re.findall(r"[a-z0-9]+", plain) if len(word) >= 2]


class EchoGuard:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.spoken = []  # (time, words) of what the avatar said
        self.echo_messages = []  # word lists of heard messages that were echo

    def note_spoken(self, text):
        now = self.clock()
        words = words_of(text)
        if words:
            self.spoken.append((now, words))
        self.spoken = [(at, said) for at, said in self.spoken if now - at <= SPOKEN_MEMORY_SECONDS]

    def recent_spoken_words(self):
        now = self.clock()
        recent = set()
        for at, said in self.spoken:
            if now - at <= SPOKEN_MEMORY_SECONDS:
                recent.update(said)
        return recent

    def is_echo(self, heard_text):
        heard = words_of(heard_text)
        if not heard:
            return True
        recent = self.recent_spoken_words()
        if not recent:
            return False
        shared = sum(1 for word in heard if word in recent)
        return shared / len(heard) >= ECHO_OVERLAP

    def remember_echo(self, heard_text):
        heard = words_of(heard_text)
        if heard:
            self.echo_messages.append(heard)
            self.echo_messages = self.echo_messages[-10:]

    def was_echo(self, message):
        """A finished user message that is one of the echoes heard while the avatar spoke (or made of them)."""
        message_words = words_of(message)
        if not message_words or not self.echo_messages:
            return False
        echo_words = set(word for echo in self.echo_messages for word in echo)
        shared = sum(1 for word in message_words if word in echo_words)
        return shared / len(message_words) >= ECHO_OVERLAP
