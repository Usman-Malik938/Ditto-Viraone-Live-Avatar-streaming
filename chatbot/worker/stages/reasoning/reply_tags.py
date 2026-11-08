from worker import config


class ReplyTagFilter:
    """Removes the fixed-message tags (such as [DECLINE]) from the streamed reply and remembers which were
    there. The LLM only decides that a fixed message is needed; the wording comes from the client profile, so it never
    drifts. Text that could be the start of a tag is held back until the next chunk shows whether it is."""

    def __init__(self):
        self.held_text = ""
        self.found_kinds = set()
        self.tags_by_kind = {}
        for message_kind in config.FIXED_MESSAGE_KINDS:
            self.tags_by_kind[message_kind] = f"[{message_kind.upper()}]"

    def add_text(self, text):
        """Returns the part of the text that is safe to show and speak now."""
        self.held_text += text
        for message_kind, tag in self.tags_by_kind.items():
            tag_position = self.held_text.find(tag)
            while tag_position != -1:
                self.found_kinds.add(message_kind)
                self.held_text = self.held_text[:tag_position] + self.held_text[tag_position + len(tag):]
                tag_position = self.held_text.find(tag)

        held_length = self.length_of_possible_tag_start()
        releasable_text = self.held_text[:len(self.held_text) - held_length]
        self.held_text = self.held_text[len(self.held_text) - held_length:]
        return releasable_text

    def finish(self):
        remaining_text = self.held_text
        self.held_text = ""
        return remaining_text

    def chosen_message_kind(self):
        """The highest-priority fixed message the reply asked for, or None."""
        for message_kind in config.FIXED_MESSAGE_KINDS:
            if message_kind in self.found_kinds:
                return message_kind
        return None

    def length_of_possible_tag_start(self):
        longest_length = 0
        for tag in self.tags_by_kind.values():
            for length in range(min(len(tag) - 1, len(self.held_text)), 0, -1):
                if tag.startswith(self.held_text[-length:]):
                    longest_length = max(longest_length, length)
                    break
        return longest_length
