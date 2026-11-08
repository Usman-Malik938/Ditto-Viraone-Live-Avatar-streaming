import logging
import time

logger = logging.getLogger(__name__)


class ReplyTimeline:
    """Times one voice reply from the moment the agent starts on it. Feeds the test console's inspector (knowledge
    used, step timings) and logs whether speech streamed: audio that starts while the LLM is still writing proves
    the TTS speaks text as it arrives. The avatar's own hop is logged by the avatar stage."""

    def __init__(self, question="", user_speech_ended_at=None):
        self.started_at = time.perf_counter()
        self.question = question
        self.user_speech_ended_at = user_speech_ended_at
        self.passages = []
        self.retrieval_seconds = None
        self.first_text_at = None
        self.last_text_at = None
        self.text_chunk_count = 0
        self.first_audio_at = None
        self.audio_chunks_before_text_finished = 0
        self.first_face_at = None

    def has_reply_text(self):
        """False for the greeting and other fixed speech, which have no question or knowledge to show."""
        return self.first_text_at is not None

    def note_passages(self, passages, retrieval_seconds):
        self.passages = passages
        self.retrieval_seconds = retrieval_seconds

    def note_text(self):
        now = time.perf_counter()
        if self.first_text_at is None:
            self.first_text_at = now
        self.last_text_at = now
        self.text_chunk_count += 1

    def note_audio(self, text_still_arriving):
        if self.first_audio_at is None:
            self.first_audio_at = time.perf_counter()
        if text_still_arriving:
            self.audio_chunks_before_text_finished += 1

    def note_face_frame(self):
        if self.first_face_at is None:
            self.first_face_at = time.perf_counter()

    def build_details(self, interrupted):
        """Seconds from the moment the agent started the reply, plus how long the end-of-turn wait was before that."""
        end_of_turn_wait_seconds = 0.0
        if self.user_speech_ended_at is not None:
            end_of_turn_wait_seconds = max(0.0, self.started_at - self.user_speech_ended_at)
        passage_dictionaries = []
        for passage in self.passages:
            passage_dictionaries.append(passage.as_dictionary())
        return {
            "question": self.question,
            "interrupted": interrupted,
            "passages": passage_dictionaries,
            "end_of_turn_wait_seconds": end_of_turn_wait_seconds,
            "retrieval_seconds": self.retrieval_seconds,
            "first_token_seconds": self.seconds_since_start(self.first_text_at),
            "first_audio_seconds": self.seconds_since_start(self.first_audio_at),
            "first_face_seconds": self.seconds_since_start(self.first_face_at),
            "total_seconds": self.seconds_since_start(self.last_text_at),
        }

    def seconds_since_start(self, moment):
        if moment is None:
            return None
        return moment - self.started_at

    def log_streaming_summary(self):
        if self.first_text_at is None or self.first_audio_at is None:
            return
        first_text_seconds = self.first_text_at - self.started_at
        last_text_seconds = self.last_text_at - self.started_at
        first_audio_seconds = self.first_audio_at - self.started_at
        if first_audio_seconds < last_text_seconds:
            verdict = f"STREAMING: speech started {last_text_seconds - first_audio_seconds:.2f} s before the LLM finished"
        else:
            verdict = "NOT streaming: speech started only after the LLM finished"
        logger.info(
            "reply timing (from reply start): LLM first text %.2f s, LLM finished %.2f s (%d chunks), first audio %.2f s, "
            "%d audio frames produced while text was still arriving -> %s",
            first_text_seconds, last_text_seconds, self.text_chunk_count, first_audio_seconds,
            self.audio_chunks_before_text_finished, verdict,
        )
