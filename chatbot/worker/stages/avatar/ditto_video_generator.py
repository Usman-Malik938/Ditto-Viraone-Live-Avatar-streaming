import asyncio
import io
import logging
import time

from livekit import rtc
from livekit.agents.voice.avatar import AudioSegmentEnd, VideoGenerator
from PIL import Image

from worker import config

logger = logging.getLogger(__name__)


class AnswerProgress:
    """One spoken answer on its way through the avatar: how much speech went up, how much came back on frames."""

    def __init__(self, answer_number):
        self.answer_number = answer_number
        self.first_sent_at = time.perf_counter()
        self.sent_samples = 0
        self.end_sent_at = None
        self.returned_samples = 0
        self.first_frame_at = None
        self.frames_while_sending = 0

    def all_speech_returned(self):
        # A frame is labelled by its first sample, so the answer's last few samples may ride on the next answer's frame.
        return self.end_sent_at is not None and self.returned_samples >= self.sent_samples - config.AVATAR_AUDIO_SAMPLES_PER_FRAME

    def is_overdue(self, now):
        if self.end_sent_at is None:
            return False
        expected_end = max(self.first_sent_at + self.sent_samples / config.AVATAR_SAMPLE_RATE, self.end_sent_at)
        return now > expected_end + config.AVATAR_ANSWER_END_GRACE_SECONDS

    def log_streaming_summary(self):
        if self.first_frame_at is None:
            return
        first_frame_seconds = self.first_frame_at - self.first_sent_at
        sending_seconds = self.end_sent_at - self.first_sent_at
        if self.frames_while_sending:
            verdict = "STREAMING: the face spoke while its voice was still arriving"
        else:
            verdict = "NOT streaming: the face started only after all the voice had arrived"
        logger.info(
            "avatar timing (answer %d): voice sent over %.2f s, first face frame %.2f s after the first voice chunk, "
            "%d face frames while voice was still arriving -> %s",
            self.answer_number, sending_seconds, first_frame_seconds, self.frames_while_sending, verdict,
        )


class JpegFrameDecoder:
    """Turns Ditto's JPEG frames into LiveKit video frames, off the event loop."""

    async def decode(self, jpeg_bytes):
        return await asyncio.to_thread(self.decode_now, jpeg_bytes)

    def decode_now(self, jpeg_bytes):
        image = Image.open(io.BytesIO(jpeg_bytes)).convert("RGBA")
        return rtc.VideoFrame(image.width, image.height, rtc.VideoBufferType.RGBA, image.tobytes())


class DittoVideoGenerator(VideoGenerator):
    """LiveKit's avatar slot. The agent's voice goes to Ditto chunk by chunk as ElevenLabs produces it; Ditto's frames
    come back 25 per second, each with its own 40 ms of audio, and only that audio is played, so lips and voice can't
    drift apart. Idle frames and frames of an interrupted answer are shown silent."""

    def __init__(self, connection, frame_decoder):
        self.connection = connection
        self.frame_decoder = frame_decoder
        # Ditto's frame rate paces this queue; the bound pushes back on the connection if publishing falls behind.
        self.output_frames = asyncio.Queue(maxsize=config.AVATAR_FRAME_RATE)
        self.sending_answer_number = 1
        self.sending_answer = None
        self.answers_in_flight = {}
        self.on_first_answer_frame = None
        self.receive_task = None

    def start(self):
        self.receive_task = asyncio.create_task(self.receive_frames())

    async def push_audio(self, frame):
        if isinstance(frame, AudioSegmentEnd):
            await self.end_answer()
            return
        if self.sending_answer is None:
            self.sending_answer = AnswerProgress(self.sending_answer_number)
            self.answers_in_flight[self.sending_answer_number] = self.sending_answer
        self.sending_answer.sent_samples += frame.samples_per_channel
        await self.connection.send_speech(frame.data.tobytes())

    async def end_answer(self):
        if self.sending_answer is None:
            return
        self.sending_answer.end_sent_at = time.perf_counter()
        await self.connection.end_answer()
        self.sending_answer = None
        self.sending_answer_number += 1

    async def clear_buffer(self):
        """The user interrupted: Ditto drops the queued voice and silences what it hasn't sent yet; frames already
        here are dropped. The service closes the current answer number on interrupt, so this one moves on too."""
        await self.connection.interrupt()
        self.sending_answer = None
        self.sending_answer_number += 1
        self.answers_in_flight.clear()
        while not self.output_frames.empty():
            self.output_frames.get_nowait()

    def __aiter__(self):
        return self.stream_output()

    async def stream_output(self):
        while True:
            output_frame = await self.output_frames.get()
            if output_frame is None:
                return
            yield output_frame

    async def receive_frames(self):
        try:
            async for ditto_frame in self.connection.frames():
                await self.output_frames.put(await self.frame_decoder.decode(ditto_frame.jpeg_bytes))
                answer = self.answers_in_flight.get(ditto_frame.answer_number)
                if answer is not None:
                    await self.play_answer_audio(answer, ditto_frame)
                await self.finish_overdue_answers()
            logger.warning("the avatar service closed the stream; the face stops for the rest of this call")
        except Exception:
            logger.exception("the avatar stream failed; the face stops for the rest of this call")
        await self.output_frames.put(None)

    async def play_answer_audio(self, answer, ditto_frame):
        now = time.perf_counter()
        if answer.first_frame_at is None:
            answer.first_frame_at = now
            if self.on_first_answer_frame is not None:
                self.on_first_answer_frame()
        if answer.end_sent_at is None:
            answer.frames_while_sending += 1
        samples_per_frame = config.AVATAR_AUDIO_SAMPLES_PER_FRAME
        await self.output_frames.put(rtc.AudioFrame(ditto_frame.pcm16_bytes, config.AVATAR_SAMPLE_RATE, 1, samples_per_frame))
        answer.returned_samples += samples_per_frame
        if answer.all_speech_returned():
            await self.finish_answer(answer)

    async def finish_answer(self, answer):
        del self.answers_in_flight[answer.answer_number]
        answer.log_streaming_summary()
        await self.output_frames.put(AudioSegmentEnd())

    async def finish_overdue_answers(self):
        now = time.perf_counter()
        overdue_answers = []
        for answer in self.answers_in_flight.values():
            if answer.is_overdue(now):
                overdue_answers.append(answer)
        for answer in overdue_answers:
            logger.warning("avatar answer %d: its last frames never arrived; counted as played", answer.answer_number)
            await self.finish_answer(answer)

    async def close(self):
        if self.receive_task is not None:
            self.receive_task.cancel()
        await self.connection.close()
