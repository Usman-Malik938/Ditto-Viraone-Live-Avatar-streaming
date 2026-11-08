import asyncio
import json
import struct

import aiohttp

from worker import config

# protocol 2 frame: b"V" + uint32 frame index + uint32 answer number + uint32 JPEG length + JPEG + 640 PCM16 samples
FRAME_HEADER = struct.Struct("<III")
FRAME_HEADER_START = 1
FRAME_PAYLOAD_START = FRAME_HEADER_START + FRAME_HEADER.size


class AvatarServiceUnavailable(Exception):
    """The avatar service refused or couldn't be reached; the call goes on voice only. busy: another call holds the
    GPU stream (close code 4409), which frees up once that call's stream closes."""

    def __init__(self, message, busy=False):
        super().__init__(message)
        self.busy = busy


class PortraitUploader:
    """Gives the avatar service the portrait to animate and returns its id. The service can't fetch our file itself."""

    async def upload(self, portrait_path):
        upload_url = config.AVATAR_SERVICE_URL.replace("wss://", "https://", 1).replace("ws://", "http://", 1) + "/v1/avatars"
        form = aiohttp.FormData()
        form.add_field("file", portrait_path.read_bytes(), filename=portrait_path.name)
        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(upload_url, data=form, headers={"x-api-key": config.AVATAR_SERVICE_API_KEY},
                                             timeout=aiohttp.ClientTimeout(total=config.AVATAR_CONNECT_TIMEOUT_SECONDS)) as response:
                    if response.status != 200:
                        raise AvatarServiceUnavailable(f"portrait upload refused ({response.status}): {await response.text()}")
                    return (await response.json())["avatar_id"]
        except (aiohttp.ClientError, TimeoutError, OSError) as upload_error:
            raise AvatarServiceUnavailable(f"can't upload the portrait to {upload_url}: {upload_error}")


class DittoFrame:
    def __init__(self, frame_index, answer_number, jpeg_bytes, pcm16_bytes):
        self.frame_index = frame_index
        # 0 = idle frame; otherwise the answer whose speech this frame carries
        self.answer_number = answer_number
        self.jpeg_bytes = jpeg_bytes
        self.pcm16_bytes = pcm16_bytes


class DittoConnection:
    """One live stream to the Ditto avatar service: speech audio up, frames (each with its own 40 ms of audio) down.
    The service numbers answers 1, 2, ... in the order they are sent; end_answer() and interrupt() each close one."""

    def __init__(self):
        self.http_session = None
        self.websocket = None
        # Speech, answer ends and interrupts must reach the service whole and in order: the service numbers answers by it.
        self.send_lock = asyncio.Lock()
        self.video_width = 0
        self.video_height = 0

    async def open(self, avatar_id):
        query = {
            "token": config.AVATAR_SERVICE_API_KEY,
            "avatar_id": avatar_id,
            "protocol": str(config.AVATAR_STREAM_PROTOCOL),
        }
        self.http_session = aiohttp.ClientSession()
        try:
            self.websocket = await self.http_session.ws_connect(
                config.AVATAR_SERVICE_URL + "/v1/stream", params=query, max_msg_size=0, heartbeat=20,
                timeout=aiohttp.ClientWSTimeout(ws_close=5), compress=0,
            )
            ready_message = await self.websocket.receive(timeout=config.AVATAR_CONNECT_TIMEOUT_SECONDS)
        except (aiohttp.ClientError, TimeoutError, OSError) as connection_error:
            await self.close()
            raise AvatarServiceUnavailable(f"can't reach {config.AVATAR_SERVICE_URL}: {connection_error}")
        if ready_message.type != aiohttp.WSMsgType.TEXT:
            reason = self.websocket.close_code
            await self.close()
            raise AvatarServiceUnavailable(f"the avatar service closed the stream (code {reason}); 4409 = another call is using it",
                                           busy=reason == 4409)
        ready = json.loads(ready_message.data)
        if ready.get("protocol") != config.AVATAR_STREAM_PROTOCOL:
            await self.close()
            raise AvatarServiceUnavailable("the avatar service is an older version without answer numbers; update it")
        self.video_width = ready["width"]
        self.video_height = ready["height"]
        return ready

    async def send_speech(self, pcm16_bytes):
        async with self.send_lock:
            await self.websocket.send_bytes(pcm16_bytes)

    async def end_answer(self):
        await self.send_command("segment_end")

    async def interrupt(self):
        await self.send_command("interrupt")

    async def send_command(self, command):
        async with self.send_lock:
            await self.websocket.send_str(json.dumps({"type": command}))

    async def frames(self):
        """Frames as the service sends them, 25 per second, until the stream closes."""
        async for message in self.websocket:
            if message.type != aiohttp.WSMsgType.BINARY:
                continue
            payload = message.data
            frame_index, answer_number, jpeg_length = FRAME_HEADER.unpack_from(payload, FRAME_HEADER_START)
            jpeg_end = FRAME_PAYLOAD_START + jpeg_length
            yield DittoFrame(frame_index, answer_number, payload[FRAME_PAYLOAD_START:jpeg_end], payload[jpeg_end:])

    async def close(self):
        if self.websocket is not None and not self.websocket.closed:
            try:
                await self.websocket.send_str(json.dumps({"type": "stop"}))
            except (aiohttp.ClientError, ConnectionError):
                pass
            await self.websocket.close()
        if self.http_session is not None:
            await self.http_session.close()
