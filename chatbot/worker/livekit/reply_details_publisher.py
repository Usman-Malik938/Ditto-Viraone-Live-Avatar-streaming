import asyncio
import json
import logging

from worker import config

logger = logging.getLogger(__name__)


class ReplyDetailsPublisher:
    """Sends each voice reply's details (knowledge used, timings) to the page on a text-stream topic. Sending is
    fire-and-forget, so a slow or failed send never holds up the conversation."""

    def __init__(self, room):
        self.room = room
        self.pending_sends = set()

    def publish(self, details):
        send_task = asyncio.create_task(self.send(details))
        self.pending_sends.add(send_task)
        send_task.add_done_callback(self.pending_sends.discard)

    async def send(self, details):
        try:
            await self.room.local_participant.send_text(json.dumps(details), topic=config.REPLY_DETAILS_TOPIC, compress=False)
        except Exception as send_error:
            logger.warning("could not send reply details to the page: %s", send_error)
