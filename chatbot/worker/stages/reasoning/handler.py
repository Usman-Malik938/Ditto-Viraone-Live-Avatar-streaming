from worker import config
from worker.stages.reasoning.conversation_session import ConversationSession
from worker.stages.reasoning.knowledge_lookup import KnowledgeLookup, RetrievalBackend
from worker.stages.reasoning.language_model_provider import LanguageModelProvider
from worker.stages.reasoning.live_reply_stream import LiveReplyStream
from worker.stages.reasoning.text_chat_console import TextChatConsole


class ReasoningStage:
    """Produces the agent's streamed reply from the conversation and the knowledge base of its language."""

    def __init__(self):
        self.retrieval_backend = None
        self.knowledge_lookups = {}

    def rebuild_knowledge_indexes(self):
        """Rebuilds every language's collection. Returns the chunk count per language name."""
        chunk_counts = {}
        for language in config.LANGUAGES.values():
            chunk_counts[language.name] = len(self.get_knowledge_lookup(language).rebuild_index())
        return chunk_counts

    def start_conversation(self, language=None, model_choice=None):
        if language is None:
            language = config.LANGUAGES[config.DEFAULT_LANGUAGE_CODE]
        language_model_provider = LanguageModelProvider(model_choice)
        return ConversationSession(
            language_model_provider.build(),
            language_model_provider.completion_options(),
            self.get_knowledge_lookup(language),
            language.system_prompt_path.read_text(encoding="utf-8"),
            language.core_facts_path.read_text(encoding="utf-8").strip(),
            language,
            config.CLIENT_PROFILE,
            language_model_provider.model_choice,
        )

    def stream_live_reply(self, conversation_session, history, user_message, on_passages):
        """The voice agent's reply as an async stream of text. history is what the user actually heard (LiveKit cuts an
        interrupted reply to the words spoken), so it replaces the session's own record for this reply.
        on_passages(passages, retrieval_seconds) runs on the agent's event loop once the knowledge search is done."""
        return LiveReplyStream(conversation_session.with_history(history), user_message, on_passages).stream_text()

    def build_live_language_model(self, model_choice=None):
        """LiveKit refuses to reply without an LLM object, though the agent writes replies through
        stream_live_reply instead (knowledge lookup, reply tags, fixed messages). Same model, so nothing else differs."""
        from livekit.plugins import openai as livekit_openai
        language_model_provider = LanguageModelProvider(model_choice)
        base_url = None
        if language_model_provider.model_choice.provider == "groq":
            base_url = config.GROQ_BASE_URL
        return livekit_openai.LLM(model=language_model_provider.model_choice.model, api_key=language_model_provider.api_key(),
                                  base_url=base_url)

    def run_text_chat(self, language):
        TextChatConsole(self.start_conversation(language)).run()

    def get_knowledge_lookup(self, language):
        """Loaded on first use and kept: the shared backend is slow to load and must exist once per process.
        Not locked, so the web server warms every language up before it takes requests."""
        if self.retrieval_backend is None:
            self.retrieval_backend = RetrievalBackend()
        if language.code not in self.knowledge_lookups:
            self.knowledge_lookups[language.code] = KnowledgeLookup(self.retrieval_backend, language)
        return self.knowledge_lookups[language.code]
