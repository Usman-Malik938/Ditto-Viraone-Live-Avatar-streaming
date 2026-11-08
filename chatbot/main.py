"""Single entry point. Usage: python main.py <process>, where process is one of PROCESS_NAMES.
'web' runs the test console, the voice page, the voice agent and the local LiveKit server together.
'agent' runs the voice agent alone and passes the words after it to LiveKit's own command line: console, dev, start
or download-files."""
import argparse
import sys

from worker import config

PROCESS_NAMES = ["agent", "speech", "index-knowledge", "chat", "web", "reasoning-test", "complex-rag-test", "retrieval-test"]


class ProcessLauncher:
    """Starts one named process. Imports stay inside each branch so the agent never loads GPU libraries."""

    def launch(self, process_name, language_code, case_names, agent_arguments):
        if process_name == "agent":
            from worker.handler import VoiceAgentWorker
            sys.argv = [sys.argv[0]] + agent_arguments
            VoiceAgentWorker().run()
        elif process_name == "speech":
            from worker.stages.listening.handler import ListeningStage
            ListeningStage().start_speech_server()
        elif process_name == "index-knowledge":
            from worker.stages.reasoning.handler import ReasoningStage
            chunk_counts = ReasoningStage().rebuild_knowledge_indexes()
            for language_name, chunk_count in chunk_counts.items():
                print(f"{language_name}: indexed {chunk_count} knowledge chunks")
        elif process_name == "chat":
            from worker.stages.reasoning.handler import ReasoningStage
            if language_code is None:
                language_code = config.DEFAULT_LANGUAGE_CODE
            ReasoningStage().run_text_chat(config.LANGUAGES[language_code])
        elif process_name == "web":
            from worker.web.web_stack import WebStack
            WebStack().run()
        elif process_name == "reasoning-test":
            from evaluation.reasoning_test import ReasoningTest
            ReasoningTest().run()
        elif process_name == "complex-rag-test":
            from evaluation.complex_rag_test import ComplexRagTest
            ComplexRagTest(case_names).run()
        elif process_name == "retrieval-test":
            from evaluation.retrieval_test import RetrievalTest
            RetrievalTest().run()


if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser(description="Avatar chatbot processes")
    argument_parser.add_argument("process", choices=PROCESS_NAMES)
    argument_parser.add_argument("--language", choices=list(config.LANGUAGES), help="conversation language for 'chat' (default: DEFAULT_LANGUAGE_CODE)")
    argument_parser.add_argument("--cases", nargs="+", help="'complex-rag-test' only: run just these case names (saves paid model calls)")
    arguments, unparsed_arguments = argument_parser.parse_known_args()
    if unparsed_arguments and arguments.process != "agent":
        argument_parser.error(f"unrecognized arguments: {' '.join(unparsed_arguments)}")
    ProcessLauncher().launch(arguments.process, arguments.language, arguments.cases, unparsed_arguments)
