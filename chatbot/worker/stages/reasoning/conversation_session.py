import time

import openai

from worker import config
from worker.stages.reasoning.reply_tags import ReplyTagFilter


class ReplyResult:
    """fixed_message_kind is 'decline' or None (a fully generated reply)."""

    def __init__(self, text, passages, fixed_message_kind, retrieval_seconds, first_token_seconds, total_seconds):
        self.text = text
        self.passages = passages
        self.fixed_message_kind = fixed_message_kind
        self.retrieval_seconds = retrieval_seconds
        self.first_token_seconds = first_token_seconds
        self.total_seconds = total_seconds


class ConversationSession:
    """One customer conversation: keeps the history, retrieves passages per turn and streams the reply."""

    def __init__(self, language_model_client, completion_options, knowledge_lookup, system_prompt_template, core_facts,
                 language, client_profile, model_choice):
        self.language_model_client = language_model_client
        self.model_choice = model_choice
        self.completion_options = completion_options
        self.knowledge_lookup = knowledge_lookup
        self.system_prompt_template = system_prompt_template
        self.core_facts = core_facts
        self.language = language
        self.client_profile = client_profile
        self.history = [{"role": "assistant", "content": language.greeting}]

    def with_history(self, history):
        """A copy that shares the model client and knowledge lookup but has its own history, so two replies can run at
        once without touching each other's state (the voice agent may start one before the user has finished)."""
        session_copy = ConversationSession(self.language_model_client, self.completion_options, self.knowledge_lookup,
                                           self.system_prompt_template, self.core_facts, self.language, self.client_profile,
                                           self.model_choice)
        session_copy.history = list(history)
        return session_copy

    def replace_history(self, history):
        """Records what a voice call actually said, so typing after the call continues from there."""
        self.history = list(history)

    def reply(self, user_message, on_text=None, on_passages=None):
        started_at = time.perf_counter()
        avatar_question = self.last_avatar_question()
        passages = self.knowledge_lookup.find_relevant_passages(
            user_message, self.build_search_query(user_message, avatar_question), answers_avatar_question=bool(avatar_question))
        retrieval_seconds = time.perf_counter() - started_at
        if on_passages is not None:
            on_passages(passages, retrieval_seconds)

        self.history.append({"role": "user", "content": user_message})
        messages = [{"role": "system", "content": self.build_system_prompt(passages)}]
        messages.extend(self.history[-config.CONVERSATION_HISTORY_MAX_MESSAGES:])

        reply_parts = []
        for attempt in range(config.LLM_FAILED_TOOL_CALL_RETRIES + 1):
            try:
                tag_filter, first_token_seconds = self.stream_model_reply(messages, reply_parts, on_text, started_at)
                break
            except openai.APIError as api_error:
                if reply_parts or getattr(api_error, "code", None) != "tool_use_failed":
                    raise
                tag_filter = self.tags_from_failed_tool_call(api_error)
                first_token_seconds = None
                if tag_filter.chosen_message_kind() is not None:
                    break
                if attempt == config.LLM_FAILED_TOOL_CALL_RETRIES:
                    raise

        fixed_message_kind = tag_filter.chosen_message_kind()
        generated_text = "".join(reply_parts).strip()
        if fixed_message_kind is None and not generated_text:
            # The model's built-in safety training sometimes refuses with an empty reply (e.g. on suicide messages).
            fixed_message_kind = "decline"
        if fixed_message_kind is not None:
            if first_token_seconds is None:
                first_token_seconds = time.perf_counter() - started_at
            fixed_message = self.client_profile.fixed_message(fixed_message_kind, self.language.code)
            self.emit_fixed_message(fixed_message, reply_parts, on_text)

        reply_text = "".join(reply_parts).strip()
        self.history.append({"role": "assistant", "content": reply_text})
        return ReplyResult(reply_text, passages, fixed_message_kind, retrieval_seconds, first_token_seconds,
                           time.perf_counter() - started_at)

    def stream_model_reply(self, messages, reply_parts, on_text, started_at):
        """Streams the model's text with the tags removed. Returns the tag filter and the first-token time."""
        stream = self.language_model_client.chat.completions.create(messages=messages, stream=True, **self.completion_options)
        tag_filter = ReplyTagFilter()
        first_token_seconds = None
        for event in stream:
            if not event.choices:
                continue
            delta_text = event.choices[0].delta.content
            if not delta_text:
                continue
            visible_text = tag_filter.add_text(delta_text)
            if visible_text and first_token_seconds is None:
                first_token_seconds = time.perf_counter() - started_at
            self.emit_text(visible_text, reply_parts, on_text)
        self.emit_text(tag_filter.finish(), reply_parts, on_text)
        return tag_filter, first_token_seconds

    def tags_from_failed_tool_call(self, api_error):
        """When the whole reply is just a tag, gpt-oss sometimes sends it as a tool call, e.g.
        {"name": "assistant", "arguments": "[DECLINE]"}, and Groq fails the request with tool_use_failed.
        The tag is still in the error's failed_generation; only the tags are kept, the JSON around them is dropped."""
        tag_filter = ReplyTagFilter()
        failed_generation = ""
        if isinstance(api_error.body, dict):
            failed_generation = str(api_error.body.get("failed_generation", ""))
        tag_filter.add_text(failed_generation)
        return tag_filter

    def emit_text(self, text, reply_parts, on_text):
        if not text:
            return
        reply_parts.append(text)
        if on_text is not None:
            on_text(text)

    def emit_fixed_message(self, fixed_message, reply_parts, on_text):
        """The fixed message goes into the history too, so the model sees what the visitor was actually told."""
        text_so_far = "".join(reply_parts)
        if text_so_far.strip() and not text_so_far[-1].isspace():
            fixed_message = " " + fixed_message
        self.emit_text(fixed_message, reply_parts, on_text)

    def build_search_query(self, user_message, avatar_question):
        """Adds the previous customer message so follow-ups like 'and how much is that?' still find the right topic,
        and the avatar's question so a bare answer like 'new' searches as 'new website or redesign? new'."""
        previous_user_message = ""
        for message in reversed(self.history):
            if message["role"] == "user":
                previous_user_message = message["content"]
                break
        return self.knowledge_lookup.build_search_query(previous_user_message, user_message, avatar_question)

    def last_avatar_question(self):
        """The question the avatar's last reply ended on, or "" when it did not end on one. The opening greeting and
        the fixed decline message ("What would you like to know?") don't count: they ask for a topic, they have none."""
        if len(self.history) < 2:
            return ""
        last_reply = self.history[-1]["content"].strip()
        if self.history[-1]["role"] != "assistant" or not last_reply.endswith("?"):
            return ""
        for message_kind in config.FIXED_MESSAGE_KINDS:
            if last_reply.endswith(self.client_profile.fixed_message(message_kind, self.language.code)):
                return ""
        question_start = 0
        for position in range(len(last_reply) - 1):
            if last_reply[position] in ".!?" and last_reply[position + 1] == " ":
                question_start = position + 2
        return last_reply[question_start:]

    def build_system_prompt(self, passages):
        passage_texts = []
        for passage in passages:
            passage_texts.append(passage.as_prompt_text())
        knowledge_text = "\n\n".join(passage_texts)
        if not knowledge_text:
            knowledge_text = self.language.no_passages_text
        return self.system_prompt_template.format(
            core_facts=self.core_facts,
            knowledge_passages=knowledge_text,
            contact_buttons=", ".join(self.client_profile.contact_button_labels(self.language.code)),
        )
