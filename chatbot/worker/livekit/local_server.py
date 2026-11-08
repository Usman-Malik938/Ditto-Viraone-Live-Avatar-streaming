import socket
import subprocess
import time
from urllib.parse import urlparse

from worker import config


def is_port_listening(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


class LocalLiveKitServer:
    """Starts `livekit-server --dev` (or with LIVEKIT_SERVER_CONFIG_PATH) unless one is already listening, and stops only
    a server it started itself.
    Its log goes to a file so the terminal shows only the app."""

    def __init__(self):
        livekit_address = urlparse(config.LIVEKIT_URL)
        self.host = livekit_address.hostname
        self.port = livekit_address.port
        self.process = None
        self.log_file = None

    def is_installed(self):
        return config.LIVEKIT_SERVER_PATH.exists()

    def missing_server_notice(self):
        return (f"Voice is off: no LiveKit server at {config.LIVEKIT_SERVER_PATH}. Download "
                f"{config.LIVEKIT_SERVER_DOWNLOAD_URL} and unzip it into {config.LIVEKIT_SERVER_PATH.parent}.")

    def ensure_running(self):
        """True once a server is listening. Only a local address can be started here; a remote one is assumed up."""
        if self.host not in ("localhost", "127.0.0.1"):
            return True
        if is_port_listening(self.host, self.port):
            print(f"LiveKit server: already running on port {self.port}", flush=True)
            return True
        if not self.is_installed():
            print(self.missing_server_notice(), flush=True)
            return False
        self.log_file = open(config.LIVEKIT_SERVER_LOG_PATH, "w", encoding="utf-8")
        server_arguments = ["--dev"]
        if config.LIVEKIT_SERVER_CONFIG_PATH:
            server_arguments = ["--config", config.LIVEKIT_SERVER_CONFIG_PATH]
        self.process = subprocess.Popen([str(config.LIVEKIT_SERVER_PATH)] + server_arguments, stdout=self.log_file,
                                        stderr=subprocess.STDOUT, cwd=str(config.PROJECT_ROOT))
        deadline = time.monotonic() + config.LIVEKIT_SERVER_START_TIMEOUT_SECONDS
        while not is_port_listening(self.host, self.port):
            if self.process.poll() is not None or time.monotonic() > deadline:
                print(f"Voice is off: the LiveKit server did not start; see {config.LIVEKIT_SERVER_LOG_PATH}.", flush=True)
                return False
            time.sleep(0.2)
        print(f"LiveKit server: started on port {self.port} (log: {config.LIVEKIT_SERVER_LOG_PATH})", flush=True)
        return True

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        if self.log_file is not None:
            self.log_file.close()
