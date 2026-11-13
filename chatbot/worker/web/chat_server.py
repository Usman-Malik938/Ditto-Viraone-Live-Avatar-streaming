"""Test console server: serves web/index.html and web/voice.html, streams each typed turn (knowledge, reply text) as
newline-delimited JSON events, and hands out LiveKit tokens for voice calls, which the voice agent answers."""
import base64
import hmac
import html
import json
import re
import threading
import uuid
from collections import OrderedDict
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import openai

from worker import config
from worker.livekit.access_tokens import AccessTokenIssuer
from worker.stages.avatar.handler import AvatarStage
from worker.stages.listening.handler import ListeningStage
from worker.stages.reasoning.handler import ReasoningStage
from worker.stages.reasoning.language_model_provider import LanguageModelProvider
from worker.stages.speaking.handler import SpeakingStage
from worker.web.session_guard import SessionGuard
from worker.web.text_turn import TextTurn

ACCESS_COOKIE_NAME = "viraone_access"
# Pages that use the login cookie and the browser's login box; every other path is API, portrait or the widget.
CONSOLE_PAGE_PATHS = ("/", "/index.html", "/voice", "/voice.html")
# The widget's visitor token: a random id the browser keeps (anything else falls back to the IP address).
VISITOR_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")
ACCESS_IN_ADDRESS_PATTERN = re.compile(r"access=[^&\s\"]*")
REFUSAL_MESSAGES = {
    "rate_limited": "Too many requests from this address. Wait a moment and try again.",
    "visitor_limit": "This visitor has used up the calls allowed per hour.",
    "ip_limit": "This address has used up the calls allowed per hour.",
}


class ConversationRegistry:
    """Keeps one ConversationSession per browser tab; the oldest is dropped past WEB_MAX_SESSIONS. The voice agent
    uses it too, so a call from a tab continues that tab's conversation."""

    def __init__(self, reasoning_stage):
        self.reasoning_stage = reasoning_stage
        self.sessions = OrderedDict()
        self.lock = threading.Lock()

    def get_or_create(self, session_id, language, model_choice):
        """A tab that switches language or model gets a fresh conversation, so replies from two models never mix."""
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None or session.language.code != language.code or session.model_choice.key != model_choice.key:
                self.sessions[session_id] = self.reasoning_stage.start_conversation(language, model_choice)
                if len(self.sessions) > config.WEB_MAX_SESSIONS:
                    self.sessions.popitem(last=False)
            self.sessions.move_to_end(session_id)
            return self.sessions[session_id]

    def forget(self, session_id):
        with self.lock:
            self.sessions.pop(session_id, None)


class ConsoleStages:
    """The stage facades shared by the console server and the voice agent in the same process."""

    def __init__(self):
        self.reasoning_stage = ReasoningStage()
        self.listening_stage = ListeningStage()
        self.speaking_stage = SpeakingStage()
        self.avatar_stage = AvatarStage()
        self.registry = ConversationRegistry(self.reasoning_stage)
        self.session_guard = SessionGuard(config.RATE_LIMIT_REQUESTS_PER_MINUTE, config.SESSIONS_PER_VISITOR_PER_HOUR,
                                          config.SESSIONS_PER_IP_PER_HOUR)


class ChatRequestHandler(BaseHTTPRequestHandler):
    stages = None
    # Empty when voice calls work; otherwise why they don't (shown on the page).
    voice_disabled_reason = ""
    token_issuer = None
    session_guard = None
    # True when this request signed in with the console's cookie or login box (or no password is set): the team's
    # own pages, which the per-visitor call limits don't apply to. False for the widget's ?access= requests.
    signed_in_as_team = False

    def do_GET(self):
        if not self.passes_rate_limit() or not self.is_authorized():
            return
        request_url = urlparse(self.path)
        if request_url.path == "/" or request_url.path == "/index.html":
            self.send_page(config.WEB_PAGE_PATH)
        elif request_url.path == "/voice" or request_url.path == "/voice.html":
            self.send_page(config.VOICE_PAGE_PATH)
        elif request_url.path == "/widget":
            self.send_widget_demo(parse_qs(request_url.query).get("access", [""])[0])
        elif request_url.path == "/avatar-portrait":
            self.send_portrait()
        elif request_url.path == "/api/settings":
            self.send_json(self.build_settings())
        elif request_url.path == "/api/voice-token":
            self.send_voice_token(parse_qs(request_url.query))
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        if not self.passes_rate_limit() or not self.is_authorized():
            return
        request_url = urlparse(self.path)
        if request_url.path == "/api/chat":
            request_body = self.read_json_body()
            self.stream_turn(
                str(request_body.get("session_id", "")).strip(),
                str(request_body.get("language", "")).strip(),
                str(request_body.get("model", "")).strip(),
                str(request_body.get("message", "")).strip(),
            )
        elif request_url.path == "/api/reset":
            self.stages.registry.forget(self.read_json_body().get("session_id", ""))
            self.send_json({"reset": True})
        else:
            self.send_error(404, "Not found")

    def is_authorized(self):
        """WEB_ACCESS_PASSWORD guards the page; always true when no password is set. Three ways in:
        - the private link /?access=<password> to the console or /voice: sets a login cookie for a day and redirects
          to the page without the password in the address, so the page and its API calls work without a login box;
        - ?access=<password> on any other request: the SCARD widget, which runs on another site where our cookie
          isn't sent, adds it to every request;
        - HTTP basic auth (any user name), which the browser asks for when there is no cookie."""
        self.signed_in_as_team = True
        if not config.WEB_ACCESS_PASSWORD:
            return True
        if self.matches_password(SimpleCookie(self.headers.get("Cookie", "")).get(ACCESS_COOKIE_NAME)):
            return True
        request_url = urlparse(self.path)
        link_password = parse_qs(request_url.query).get("access", [""])[0]
        if link_password and self.matches_password(link_password) and request_url.path not in CONSOLE_PAGE_PATHS:
            self.signed_in_as_team = False
            return True
        if link_password and self.matches_password(link_password):
            self.send_response(303)
            self.send_header("Set-Cookie", f"{ACCESS_COOKIE_NAME}={link_password}; Path=/; Max-Age=86400; Secure; HttpOnly; SameSite=Lax")
            self.send_header("Location", request_url.path or "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        credentials = self.headers.get("Authorization", "")
        if credentials.startswith("Basic "):
            try:
                password = base64.b64decode(credentials[6:]).decode("utf-8").partition(":")[2]
            except (ValueError, UnicodeDecodeError):
                password = ""
            if self.matches_password(password):
                return True
        self.send_response(401)
        self.send_cors_headers()
        # Only pages get the browser's login box; a widget's request to the API just fails.
        if request_url.path in CONSOLE_PAGE_PATHS:
            self.send_header("WWW-Authenticate", 'Basic realm="ViraOne avatar", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def log_message(self, format, *args):
        """The standard request log, with the access key in widget addresses blanked out."""
        super().log_message(format, *(ACCESS_IN_ADDRESS_PATTERN.sub("access=***", str(arg)) for arg in args))

    def passes_rate_limit(self):
        """Every request counts, before the password check, so guessing the access key is slow too."""
        refusal = self.session_guard.check_request(self.client_ip_address())
        if refusal is None:
            return True
        self.send_refusal(refusal)
        return False

    def client_ip_address(self):
        """The visitor's address. On RunPod every request comes through its HTTPS proxy (Cloudflare), which passes
        the original address on in these headers."""
        forwarded_address = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "").split(",")[0]
        return forwarded_address.strip() or self.client_address[0]

    def send_refusal(self, refusal):
        self.send_json(refusal.as_dictionary(REFUSAL_MESSAGES[refusal.code]), status=429,
                       extra_headers={"Retry-After": str(refusal.retry_after_seconds)})

    def send_cors_headers(self):
        """Lets the SCARD page (another site) read the widget's API answers. WIDGET_ALLOWED_ORIGINS in .env lists the
        sites allowed; "*" allows any. Without an Origin header (same-site requests) nothing is added."""
        origin = self.headers.get("Origin", "")
        if not origin:
            return
        if "*" in config.WIDGET_ALLOWED_ORIGINS or origin in config.WIDGET_ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def send_widget_demo(self, access_key):
        """The SCARD widget on a page of its own: the snippet exactly as his developer gets it, pointed at this server.
        Opened as /widget?access=<password>, the key it needs comes from the link."""
        snippet = config.WIDGET_SNIPPET_PATH.read_text(encoding="utf-8")
        snippet = snippet.replace('data-server="https://YOUR-POD-ID-8080.proxy.runpod.net"', 'data-server=""', 1)
        snippet = snippet.replace('data-key="YOUR-ACCESS-KEY"', f'data-key="{html.escape(access_key, quote=True)}"', 1)
        page = ('<!doctype html><html lang="pl"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1"><title>ViraOne avatar</title>'
                '<style>body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px 16px;'
                'font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:#f2f4f3}</style></head><body>'
                + snippet + '</body></html>').encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page)

    def matches_password(self, candidate):
        """candidate: text or a cookie morsel; compared in constant time."""
        if candidate is None:
            return False
        if hasattr(candidate, "value"):
            candidate = candidate.value
        return hmac.compare_digest(candidate.encode("utf-8"), config.WEB_ACCESS_PASSWORD.encode("utf-8"))

    def stream_turn(self, session_id, language_code, model_key, message):
        model_choice = config.find_language_model_choice(model_key)
        request_error = self.check_conversation_request(session_id, language_code, model_choice)
        if not request_error and not message:
            request_error = "Send a non-empty message."
        if request_error:
            self.send_json({"error": request_error}, status=400)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        event_writer = TurnEventWriter(self.wfile)
        conversation_session = self.stages.registry.get_or_create(session_id, config.LANGUAGES[language_code], model_choice)
        try:
            TextTurn(conversation_session, event_writer).run(message)
        except openai.RateLimitError:
            event_writer.write_error("The language model's rate or quota limit was hit. On Groq's free tier, wait about a minute; on OpenAI, check the account's credit.")
        except openai.APIConnectionError:
            event_writer.write_error("Can't reach the language model. Check the internet connection and the API key in .env.")
        except openai.APIError as api_error:
            event_writer.write_error(f"The language model returned an error: {api_error.message}")
        except ConnectionError:
            return

    def send_voice_token(self, query):
        """A fresh room per call; the agent joins every new room and reads the call settings from these attributes."""
        session_id = query.get("session_id", [""])[0].strip()
        language_code = query.get("language", [""])[0].strip()
        model_key = query.get("model", [""])[0].strip()
        speak_greeting = query.get("speak_greeting", ["true"])[0].strip()
        voice_key = query.get("voice", [""])[0].strip()
        visitor_token = query.get("visitor", [""])[0].strip()
        if self.voice_disabled_reason:
            self.send_json({"error": self.voice_disabled_reason}, status=503)
            return
        model_choice = config.find_language_model_choice(model_key)
        if not session_id:
            session_id = uuid.uuid4().hex
        request_error = self.check_conversation_request(session_id, language_code, model_choice)
        if request_error:
            self.send_json({"error": request_error}, status=400)
            return
        room_name = f"{config.VOICE_ROOM_NAME_PREFIX}{uuid.uuid4().hex[:12]}"
        if not self.signed_in_as_team:
            ip_address = self.client_ip_address()
            if not VISITOR_TOKEN_PATTERN.match(visitor_token):
                visitor_token = f"ip-{ip_address}"
            refusal = self.session_guard.start_widget_call(visitor_token, ip_address, room_name)
            if refusal is not None:
                self.send_refusal(refusal)
                return
        attributes = {
            config.VOICE_LANGUAGE_ATTRIBUTE: language_code,
            config.VOICE_MODEL_ATTRIBUTE: model_choice.key,
            config.VOICE_SESSION_ATTRIBUTE: session_id,
            config.VOICE_GREETING_ATTRIBUTE: speak_greeting,
            config.VOICE_VOICE_ATTRIBUTE: voice_key,
        }
        token = self.token_issuer.issue_token(room_name, f"{config.VOICE_VISITOR_IDENTITY_PREFIX}{uuid.uuid4().hex[:8]}", attributes)
        self.send_json({"url": config.LIVEKIT_PUBLIC_URL, "token": token, "room": room_name})

    def check_conversation_request(self, session_id, language_code, model_choice):
        """The reason a chat or call request can't be served, or "" when it can."""
        if not session_id:
            return "Send a session_id."
        if language_code not in config.LANGUAGES:
            return f"Send a language, one of: {', '.join(config.LANGUAGES)}."
        if model_choice is None:
            return "Unknown model. Reload the page to get the current model list."
        if not LanguageModelProvider(model_choice).is_available():
            return f"{model_choice.label} has no API key. Add it to .env and restart the server."
        return ""

    def build_settings(self):
        models = []
        for choice in config.LANGUAGE_MODEL_CHOICES:
            models.append({
                "key": choice.key,
                "label": choice.label,
                "model": choice.model,
                "available": LanguageModelProvider(choice).is_available(),
            })
        languages = []
        for language in config.LANGUAGES.values():
            languages.append({
                "code": language.code,
                "name": language.name,
                "greeting": language.greeting,
                "speech_recognition_model": self.stages.listening_stage.describe_speech_recognizer(language),
            })
        voices = []
        for choice in config.VOICE_CHOICES:
            voices.append({"key": choice.key, "label": choice.label})
        return {
            "languages": languages,
            # Labels and keys only: the ElevenLabs voice ids never reach the page.
            "voices": voices,
            "voice_attribute": config.VOICE_VOICE_ATTRIBUTE,
            "default_language_code": config.DEFAULT_LANGUAGE_CODE,
            "models": models,
            "default_model_key": config.DEFAULT_LANGUAGE_MODEL.key,
            "voice_enabled": not self.voice_disabled_reason,
            "voice_disabled_reason": self.voice_disabled_reason,
            "speech_output_enabled": self.stages.speaking_stage.speech_enabled,
            "speech_disabled_reason": self.stages.speaking_stage.speech_disabled_reason(),
            "avatar_enabled": self.stages.avatar_stage.enabled,
            "avatar_disabled_reason": self.stages.avatar_stage.disabled_reason(),
            "avatar_state_attribute": config.AVATAR_STATE_ATTRIBUTE,
            "avatar_portrait_url": self.portrait_url(),
            "reply_details_topic": config.REPLY_DETAILS_TOPIC,
            "target_reply_start_seconds": config.TARGET_REPLY_START_SECONDS,
            "session_max_seconds": config.SESSION_MAX_SECONDS,
            "call_end_attribute": config.CALL_END_ATTRIBUTE,
        }

    def portrait_url(self):
        if self.stages.avatar_stage.portrait_path() is None:
            return ""
        return "/avatar-portrait"

    def send_portrait(self):
        portrait_path = self.stages.avatar_stage.portrait_path()
        if portrait_path is None:
            self.send_error(404, "No portrait: set AVATAR_PORTRAIT_PATH")
            return
        content_type = "image/jpeg"
        if portrait_path.suffix.lower() == ".png":
            content_type = "image/png"
        elif portrait_path.suffix.lower() == ".webp":
            content_type = "image/webp"
        image_bytes = portrait_path.read_bytes()
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(image_bytes)))
        self.end_headers()
        self.wfile.write(image_bytes)

    def send_page(self, page_path):
        page_bytes = page_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(page_bytes)

    def send_json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        for header_name, header_value in (extra_headers or {}).items():
            self.send_header(header_name, header_value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}


class TurnEventWriter:
    """Writes one JSON object per line and flushes, so the page renders each event as it arrives."""

    def __init__(self, output_stream):
        self.output_stream = output_stream
        self.write_lock = threading.Lock()

    def write_passages(self, passages, retrieval_seconds):
        passage_dictionaries = []
        for passage in passages:
            passage_dictionaries.append(passage.as_dictionary())
        self.write_event({"type": "passages", "passages": passage_dictionaries, "retrieval_seconds": retrieval_seconds})

    def write_text(self, text):
        self.write_event({"type": "text", "text": text})

    def write_done(self, timings):
        event = {"type": "done"}
        event.update(timings)
        self.write_event(event)

    def write_error(self, message):
        self.write_event({"type": "error", "message": message})

    def write_event(self, event):
        line = (json.dumps(event) + "\n").encode("utf-8")
        with self.write_lock:
            self.output_stream.write(line)
            self.output_stream.flush()


class ConsoleHttpServer(ThreadingHTTPServer):
    """Address reuse is off: on Windows it lets a second server bind the same port, so a stale one keeps answering."""

    allow_reuse_address = False
    daemon_threads = True


class ChatServer:
    """Warms the stages up, then serves the pages. start() runs in the background, for when the voice agent owns the
    main thread."""

    def __init__(self, stages):
        self.stages = stages
        self.http_server = None

    def prepare(self, voice_disabled_reason):
        """Claims the port first, so a second copy stops before loading anything, then loads every language's knowledge
        base (and local speech model when used) before taking requests."""
        try:
            self.http_server = ConsoleHttpServer((config.WEB_SERVER_HOST, config.WEB_SERVER_PORT), ChatRequestHandler)
        except OSError:
            print(f"Port {config.WEB_SERVER_PORT} is already in use: the test console is probably running already. "
                  f"Stop it first, or set WEB_SERVER_PORT in .env.", flush=True)
            raise SystemExit(1)
        print("Loading the embedding and speech recognition models for every language...", flush=True)
        for language in config.LANGUAGES.values():
            knowledge_lookup = self.stages.reasoning_stage.get_knowledge_lookup(language)
            if not knowledge_lookup.is_indexed():
                print(f"The {language.name} knowledge base is not indexed yet. Run 'python main.py index-knowledge' first.", flush=True)
                raise SystemExit(1)
            knowledge_lookup.find_relevant_passages("warm-up search", "warm-up search")
            # Without voice calls (no LiveKit server, e.g. text-only testing) nothing listens, so skip the speech model.
            if voice_disabled_reason:
                continue
            self.stages.listening_stage.warm_up(language)
            print(f"{language.name} speech-to-text: {self.stages.listening_stage.describe_speech_recognizer(language)}", flush=True)
        if self.stages.listening_stage.speech_recognizer_notice():
            print(self.stages.listening_stage.speech_recognizer_notice(), flush=True)
        if not self.stages.speaking_stage.speech_enabled:
            print(self.stages.speaking_stage.speech_disabled_reason() + " Voice replies are text only.", flush=True)
        ChatRequestHandler.stages = self.stages
        ChatRequestHandler.voice_disabled_reason = voice_disabled_reason
        ChatRequestHandler.token_issuer = AccessTokenIssuer()
        ChatRequestHandler.session_guard = self.stages.session_guard

    def start(self):
        threading.Thread(target=self.http_server.serve_forever, daemon=True).start()
        print(f"Test console running at http://localhost:{config.WEB_SERVER_PORT} (voice page: /voice)", flush=True)

    def serve_forever(self):
        print(f"Test console running at http://localhost:{config.WEB_SERVER_PORT}", flush=True)
        self.http_server.serve_forever()
