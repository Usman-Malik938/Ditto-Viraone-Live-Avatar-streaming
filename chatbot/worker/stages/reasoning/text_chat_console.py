class TextChatConsole:
    """Terminal chat for testing the reasoning stage without voice. Type 'exit' to stop."""

    def __init__(self, conversation_session):
        self.conversation_session = conversation_session

    def run(self):
        print(f"Avatar: {self.conversation_session.language.greeting}")
        while True:
            user_message = input("You: ").strip()
            if user_message.lower() == "exit":
                break
            if not user_message:
                continue
            print("Avatar: ", end="", flush=True)
            result = self.conversation_session.reply(user_message, on_text=self.print_text)
            print(f"\n  [retrieval {result.retrieval_seconds * 1000:.0f} ms, first token {self.format_seconds(result.first_token_seconds)}, total {result.total_seconds:.2f} s]")

    def print_text(self, text):
        print(text, end="", flush=True)

    def format_seconds(self, seconds):
        if seconds is None:
            return "none"
        return f"{seconds:.2f} s"
