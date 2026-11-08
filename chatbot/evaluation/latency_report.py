from worker import config


class LatencyReport:
    """Per-stage timings from a conversation, checked against the MVP targets."""

    def __init__(self):
        self.target_reply_start_seconds = config.TARGET_REPLY_START_SECONDS
        self.target_interrupt_stop_seconds = config.TARGET_INTERRUPT_STOP_SECONDS

    def build(self, stage_timings):
        raise NotImplementedError
