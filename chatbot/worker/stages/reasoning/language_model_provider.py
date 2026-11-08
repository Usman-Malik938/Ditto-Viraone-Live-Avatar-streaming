from openai import OpenAI

from worker import config


class LanguageModelProvider:
    """Builds the LLM client and request options for one model choice. Groq speaks the OpenAI API, so one client type
    serves both providers."""

    def __init__(self, model_choice=None):
        if model_choice is None:
            model_choice = config.DEFAULT_LANGUAGE_MODEL
        self.model_choice = model_choice

    def is_available(self):
        return bool(self.api_key())

    def api_key(self):
        if self.model_choice.provider == "groq":
            return config.GROQ_API_KEY
        if self.model_choice.provider == "openai":
            return config.OPENAI_API_KEY
        raise ValueError(f"Unknown LLM provider '{self.model_choice.provider}'. Use 'groq' or 'openai'.")

    def build(self):
        if self.model_choice.provider == "groq":
            return OpenAI(api_key=self.api_key(), base_url=config.GROQ_BASE_URL)
        return OpenAI(api_key=self.api_key())

    def completion_options(self):
        options = {
            "model": self.model_choice.model,
            "max_completion_tokens": self.model_choice.max_completion_tokens,
        }
        if self.model_choice.sends_temperature:
            options["temperature"] = config.LLM_TEMPERATURE
        if self.model_choice.reasoning_effort:
            options["reasoning_effort"] = self.model_choice.reasoning_effort
        return options
