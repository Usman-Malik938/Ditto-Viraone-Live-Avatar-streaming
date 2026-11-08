from livekit import rtc
from livekit.agents.voice.avatar import AvatarOptions, AvatarRunner, QueueAudioOutput

from worker import config
from worker.stages.avatar.ditto_video_generator import DittoVideoGenerator, JpegFrameDecoder


class SyncedAvatarRunner(AvatarRunner):
    """LiveKit's AvatarRunner, publishing its two tracks with options it doesn't expose (checked on agents 1.8.11):
    one shared stream name, so the browser plays voice and face in sync; one full-quality layer at Ditto's 25 fps
    (LiveKit's presets drop small videos to 20 fps, and simulcast would send the page a lower layer)."""

    async def _publish_track(self):
        async with self._lock:
            await self._room_connected_fut
            audio_track = rtc.LocalAudioTrack.create_audio_track("avatar_audio", self._audio_source)
            audio_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE,
                                                    stream=config.AVATAR_TRACK_STREAM_NAME)
            self._audio_publication = await self._room.local_participant.publish_track(audio_track, audio_options)
            await self._audio_publication.wait_for_subscription()
            video_track = rtc.LocalVideoTrack.create_video_track("avatar_video", self._video_source)
            video_options = rtc.TrackPublishOptions(
                source=rtc.TrackSource.SOURCE_CAMERA,
                stream=config.AVATAR_TRACK_STREAM_NAME,
                simulcast=False,
                video_encoding=rtc.VideoEncoding(max_framerate=config.AVATAR_FRAME_RATE,
                                                 max_bitrate=config.AVATAR_VIDEO_MAX_BITRATE),
            )
            self._video_publication = await self._room.local_participant.publish_track(video_track, video_options)


class AvatarCall:
    """The avatar for one call. The agent's voice goes into a 16 kHz queue instead of the room; LiveKit's AvatarRunner
    feeds it to Ditto and publishes Ditto's frames and their audio into the room as one synchronised pair of tracks."""

    def __init__(self, connection):
        self.connection = connection
        self.video_generator = DittoVideoGenerator(connection, JpegFrameDecoder())
        # Playback counts as started when the first face frame with sound goes out, not when ElevenLabs sends audio,
        # so the agent's state and the word-by-word transcript follow the face.
        self.agent_voice = QueueAudioOutput(sample_rate=config.AVATAR_SAMPLE_RATE, wait_playback_start=True)
        self.runner = None

    def route_agent_voice(self, agent_session):
        """Before the session starts, so LiveKit sends the voice here rather than publishing it itself."""
        agent_session.output.audio = self.agent_voice

    async def start_publishing(self, room):
        options = AvatarOptions(
            video_width=self.connection.video_width,
            video_height=self.connection.video_height,
            video_fps=config.AVATAR_FRAME_RATE,
            audio_sample_rate=config.AVATAR_SAMPLE_RATE,
            audio_channels=1,
        )
        self.runner = SyncedAvatarRunner(room, audio_recv=self.agent_voice, video_gen=self.video_generator, options=options)
        self.video_generator.start()
        await self.runner.start()

    def report_first_face_frame_to(self, on_first_face_frame):
        """on_first_face_frame() runs once per answer, when its first speaking frame arrives."""
        self.video_generator.on_first_answer_frame = on_first_face_frame

    async def close(self):
        if self.runner is not None:
            await self.runner.aclose()
        await self.video_generator.close()
