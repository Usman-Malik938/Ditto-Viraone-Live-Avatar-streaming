"""'python main.py web': the test console, the voice page and the voice agent in one process, plus the local LiveKit
server. One process because the console and the agent share the knowledge base, which only one process may open.
Ctrl+C stops everything."""
import sys

from worker import config
from worker.livekit.local_server import LocalLiveKitServer
from worker.web.chat_server import ChatServer, ConsoleStages


class WebStack:
    def run(self):
        stages = ConsoleStages()
        livekit_server = LocalLiveKitServer()
        try:
            voice_disabled_reason = ""
            if not livekit_server.ensure_running():
                voice_disabled_reason = "Voice calls are off: the LiveKit server isn't running (see the terminal). Typing still works."
            chat_server = ChatServer(stages)
            chat_server.prepare(voice_disabled_reason)
            if voice_disabled_reason:
                chat_server.serve_forever()
                return
            chat_server.start()
            self.run_voice_agent(stages)
        except KeyboardInterrupt:
            pass
        finally:
            livekit_server.stop()

    def run_voice_agent(self, stages):
        """Owns the main thread until Ctrl+C; LiveKit's command line reads its arguments from sys.argv."""
        from worker.handler import VoiceAgentWorker
        sys.argv = [sys.argv[0], "dev", "--log-level", config.VOICE_AGENT_LOG_LEVEL]
        voice_agent_worker = VoiceAgentWorker(stages.listening_stage, stages.reasoning_stage, stages.speaking_stage,
                                              stages.registry, stages.avatar_stage, stages.session_guard)
        voice_agent_worker.run(on_registered=self.announce_ready)

    def announce_ready(self, *registration_details):
        print(f"\n=== Ready: test console http://localhost:{config.WEB_SERVER_PORT} , voice page "
              f"http://localhost:{config.WEB_SERVER_PORT}/voice . Ctrl+C stops everything. ===\n", flush=True)
