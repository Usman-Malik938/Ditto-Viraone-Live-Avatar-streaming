import asyncio
import logging
import time

from worker import config

logger = logging.getLogger(__name__)


class AvatarStage:
    """Shows the talking face: Ditto on the GPU animates the agent's voice. The avatar, not the agent, sends the voice
    to the user, so lips and voice stay in sync. Without it, calls are voice only."""

    def __init__(self):
        self.enabled = config.AVATAR_ENABLED and bool(config.AVATAR_SERVICE_API_KEY) and self.portrait_path() is not None
        # The service's id for our portrait, uploaded once per run on the first call. Calls run on separate event
        # loops, so no asyncio lock: two calls starting together would just upload it twice, which is harmless.
        self.avatar_id = None

    def disabled_reason(self):
        if not config.AVATAR_ENABLED:
            return "The avatar is off (AVATAR_ENABLED=false); calls are voice only."
        if not config.AVATAR_SERVICE_API_KEY:
            return "The avatar is off: set AVATAR_SERVICE_API_KEY in .env."
        if self.portrait_path() is None:
            return f"The avatar is off: no portrait at {config.AVATAR_PORTRAIT_PATH}."
        return ""

    def portrait_path(self):
        """The face the avatar animates and the pages show; None when the file is missing."""
        if config.AVATAR_PORTRAIT_PATH.is_file():
            return config.AVATAR_PORTRAIT_PATH
        return None

    async def join_call(self):
        """Opens the avatar stream for one call. None when the avatar is off, busy with another call or unreachable,
        and the call goes on voice only."""
        if not self.enabled:
            return None
        from worker.stages.avatar.avatar_call import AvatarCall
        from worker.stages.avatar.ditto_connection import AvatarServiceUnavailable, DittoConnection
        # A call that starts right after the last one ended can find its stream still closing: wait for it a little.
        busy_deadline = time.monotonic() + config.AVATAR_BUSY_WAIT_SECONDS
        while True:
            connection = DittoConnection()
            try:
                ready = await connection.open(await self.find_avatar_id())
                break
            except AvatarServiceUnavailable as unavailable:
                if unavailable.busy and time.monotonic() < busy_deadline:
                    await asyncio.sleep(1)
                    continue
                logger.warning("avatar unavailable, this call is voice only: %s", unavailable)
                return None
        logger.info("avatar stream ready: %sx%s, set up in %s s", ready["width"], ready["height"], ready.get("setup_s"))
        return AvatarCall(connection)

    async def find_avatar_id(self):
        from worker.stages.avatar.ditto_connection import PortraitUploader
        if self.avatar_id is None:
            self.avatar_id = await PortraitUploader().upload(self.portrait_path())
            logger.info("portrait %s uploaded to the avatar service as %s", self.portrait_path().name, self.avatar_id)
        return self.avatar_id
