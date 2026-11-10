"""Every setting for the project. Secrets and deploy-specific values come from .env; nothing else reads it."""
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

MODEL_CACHE_DIRECTORY = PROJECT_ROOT / "models"

# LiveKit. The defaults match a local `livekit-server --dev`, whose key and secret are public dev values.
LIVEKIT_URL = os.getenv("LIVEKIT_URL") or "ws://localhost:7880"
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY") or "devkey"
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET") or "secret"
# The address the browser joins calls on. Same as LIVEKIT_URL on one PC; on the RunPod pod the agent uses
# ws://localhost:7880 and the browser the pod's HTTPS link, wss://<pod-id>-7880.proxy.runpod.net.
LIVEKIT_PUBLIC_URL = os.getenv("LIVEKIT_PUBLIC_URL") or LIVEKIT_URL
# 'python main.py web' starts this downloaded server binary when none is running yet (tools/ is git-ignored).
LIVEKIT_SERVER_PATH = PROJECT_ROOT / "tools" / "livekit" / ("livekit-server.exe" if platform.system() == "Windows" else "livekit-server")
LIVEKIT_SERVER_LOG_PATH = PROJECT_ROOT / "tools" / "livekit" / "livekit-server.log"
LIVEKIT_SERVER_DOWNLOAD_URL = "https://github.com/livekit/livekit/releases/download/v1.13.11/livekit_1.13.11_windows_amd64.zip"
# Empty = `livekit-server --dev` (this PC only). The pod passes its config file (TCP media port, public IP, own keys).
LIVEKIT_SERVER_CONFIG_PATH = os.getenv("LIVEKIT_SERVER_CONFIG_PATH", "")
LIVEKIT_SERVER_START_TIMEOUT_SECONDS = 15
VOICE_AGENT_LOG_LEVEL = "INFO"
VOICE_ROOM_NAME_PREFIX = "voice-"
# Every page joining a call gets an identity starting with this; the agent ends the call when that person leaves.
VOICE_VISITOR_IDENTITY_PREFIX = "visitor-"
VOICE_TOKEN_LIFETIME_MINUTES = 60
# A page joining a call sets these participant attributes; the agent reads them to set up the conversation.
VOICE_LANGUAGE_ATTRIBUTE = "language"
VOICE_MODEL_ATTRIBUTE = "model"
# The test console's conversation id, so a call continues the conversation typed so far in that tab.
VOICE_SESSION_ATTRIBUTE = "session_id"
# "true" makes the agent speak the greeting; the test console sends "false" once a conversation has started.
VOICE_GREETING_ATTRIBUTE = "speak_greeting"
# The voice the page picked ("voice-1", ...). The page can change it during a call; the agent switches from the next
# sentence on.
VOICE_VOICE_ATTRIBUTE = "voice"
# Set by the agent on itself: "on" (video coming), "unavailable" (busy or unreachable) or "off"; the page shows the
# still portrait unless it's "on".
AVATAR_STATE_ATTRIBUTE = "viraone.avatar"
# After each voice reply the agent sends the knowledge used and the timings on this text-stream topic.
REPLY_DETAILS_TOPIC = "viraone.reply-details"

# Voice agent turn-taking (LiveKit). VAD interruption mode runs locally; the "adaptive" mode needs LiveKit Cloud.
INTERRUPTION_MODE = "vad"
# Speech shorter than this while the agent talks doesn't interrupt it.
MIN_INTERRUPTION_SECONDS = 0.3
# The longest the turn detector waits when unsure. The shortest wait is per language (LanguageSettings).
MAX_END_OF_TURN_DELAY_SECONDS = 3.0
# A sound that interrupts the agent but brings no words within this many seconds is a false alarm: the agent resumes.
FALSE_INTERRUPTION_TIMEOUT_SECONDS = 2.0
RESUME_AFTER_FALSE_INTERRUPTION = True
# Starts the LLM while the user may still be finishing: faster replies, but extra Groq calls when they keep talking.
PREEMPTIVE_GENERATION_ENABLED = (os.getenv("PREEMPTIVE_GENERATION_ENABLED") or "true").strip().lower() == "true"

# Turn bell: a short tone when the turn passes. Higher pitch = user's turn, lower = agent's turn.
TURN_BELL_ENABLED = (os.getenv("TURN_BELL_ENABLED") or "true").strip().lower() == "true"
TURN_BELL_USER_TURN_HERTZ = 880.0
TURN_BELL_AGENT_TURN_HERTZ = 587.0
TURN_BELL_SECONDS = 0.18
TURN_BELL_VOLUME = 0.25
TURN_BELL_ATTACK_SECONDS = 0.005
TURN_BELL_DECAY_PER_SECOND = 18.0
TURN_BELL_SAMPLE_RATE = 48000

# Listening, local fallback on CPU: faster-whisper. The model is chosen per language (see LANGUAGES).
SPEECH_RECOGNITION_DEVICE = os.getenv("SPEECH_RECOGNITION_DEVICE", "cpu")
SPEECH_RECOGNITION_COMPUTE_TYPE = os.getenv("SPEECH_RECOGNITION_COMPUTE_TYPE", "int8")
SPEECH_RECOGNITION_BEAM_SIZE = 1
SPEECH_RECOGNITION_HOTWORDS = "Smart4U SCARD ViraOne WordPress Elementor PMax Google Ads"
# "elevenlabs" (Scribe realtime, needs ELEVENLABS_ENABLED and a key, else falls back to local) or "local"
# (faster-whisper on each utterance the VAD cuts out).
SPEECH_TO_TEXT_PROVIDER = (os.getenv("SPEECH_TO_TEXT_PROVIDER") or "elevenlabs").strip().lower()
ELEVENLABS_REALTIME_SPEECH_TO_TEXT_MODEL = "scribe_v2_realtime"

# Listening on GPU later: Silero VAD + self-hosted Nemotron + LiveKit turn detector
SPEECH_SERVER_HOST = os.getenv("SPEECH_SERVER_HOST", "127.0.0.1")
SPEECH_SERVER_PORT = int(os.getenv("SPEECH_SERVER_PORT", "8101"))
SPEECH_MODEL_ID = "nvidia/nemotron-speech-streaming-en-0.6b"
SPEECH_CHUNK_MILLISECONDS = 160
SPEECH_SAMPLE_RATE = 16000

# Reasoning: Groq first, OpenAI when Groq limits are hit
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_TEMPERATURE = 0.4
LLM_MAX_COMPLETION_TOKENS = 600
LLM_FAILED_TOOL_CALL_RETRIES = 1
# OpenAI reasoning models count their hidden thinking against the token limit; too low a limit leaves the reply empty.
LLM_REASONING_MODEL_MAX_COMPLETION_TOKENS = 4000


@dataclass(frozen=True)
class LanguageModelChoice:
    """One model the test console can switch to. reasoning_effort is only sent when set: OpenAI's gpt-4.x models
    reject it. OpenAI's reasoning models (gpt-5 family) reject temperature, hence sends_temperature."""

    key: str
    label: str
    provider: str
    model: str
    reasoning_effort: str
    sends_temperature: bool
    max_completion_tokens: int


# Paid OpenAI models are listed but only usable when OPENAI_API_KEY is set; the console always starts on the default.
LANGUAGE_MODEL_CHOICES = [
    LanguageModelChoice("groq-gpt-oss-20b", "Groq gpt-oss-20b (free)", "groq", "openai/gpt-oss-20b", LLM_REASONING_EFFORT, True,
                        LLM_MAX_COMPLETION_TOKENS),
    LanguageModelChoice("groq-gpt-oss-120b", "Groq gpt-oss-120b (free)", "groq", "openai/gpt-oss-120b", LLM_REASONING_EFFORT, True,
                        LLM_MAX_COMPLETION_TOKENS),
    LanguageModelChoice("openai-gpt-5-mini", "OpenAI gpt-5-mini (paid)", "openai", "gpt-5-mini", "low", False,
                        LLM_REASONING_MODEL_MAX_COMPLETION_TOKENS),
    # Barely thinks before answering, so it starts sooner than "low"; same model, same price.
    LanguageModelChoice("openai-gpt-5-mini-minimal", "OpenAI gpt-5-mini, minimal thinking (paid)", "openai", "gpt-5-mini",
                        "minimal", False, LLM_REASONING_MODEL_MAX_COMPLETION_TOKENS),
    # Not a reasoning model: no hidden thinking, so text starts almost at once. Rejects reasoning_effort.
    LanguageModelChoice("openai-gpt-4.1-mini", "OpenAI gpt-4.1-mini (paid)", "openai", "gpt-4.1-mini", "", True,
                        LLM_MAX_COMPLETION_TOKENS),
]


def read_shown_model_keys():
    """MODEL_OPTIONS in .env: comma-separated keys the console dropdown offers. Empty shows every model."""
    shown_model_keys = []
    for model_key in os.getenv("MODEL_OPTIONS", "").split(","):
        if model_key.strip():
            shown_model_keys.append(model_key.strip())
    return shown_model_keys


def keep_shown_models(model_choices, shown_model_keys):
    if not shown_model_keys:
        return model_choices
    shown_choices = []
    for choice in model_choices:
        if choice.key in shown_model_keys:
            shown_choices.append(choice)
    return shown_choices


def find_default_language_model():
    """The choice matching LLM_PROVIDER and LLM_MODEL from .env, so 'chat' and the evaluations keep using it."""
    for choice in LANGUAGE_MODEL_CHOICES:
        if choice.provider == LLM_PROVIDER and choice.model == LLM_MODEL:
            return choice
    return LanguageModelChoice("env-default", f"{LLM_PROVIDER} {LLM_MODEL}", LLM_PROVIDER, LLM_MODEL, LLM_REASONING_EFFORT, True,
                               LLM_MAX_COMPLETION_TOKENS)


DEFAULT_LANGUAGE_MODEL = find_default_language_model()
# Hidden models can't be picked at all; the .env default always stays, so 'chat' and the evaluations keep working.
LANGUAGE_MODEL_CHOICES = keep_shown_models(LANGUAGE_MODEL_CHOICES, read_shown_model_keys())
if DEFAULT_LANGUAGE_MODEL not in LANGUAGE_MODEL_CHOICES:
    LANGUAGE_MODEL_CHOICES.insert(0, DEFAULT_LANGUAGE_MODEL)


def find_language_model_choice(model_key):
    """An empty key means the default model, so pages and scripts that don't send one keep working. None if unknown."""
    if not model_key:
        return DEFAULT_LANGUAGE_MODEL
    for choice in LANGUAGE_MODEL_CHOICES:
        if choice.key == model_key:
            return choice
    return None


REASONING_STAGE_DIRECTORY = PROJECT_ROOT / "worker" / "stages" / "reasoning"
KNOWLEDGE_BASE_DIRECTORY = REASONING_STAGE_DIRECTORY / "knowledge_base"

# Knowledge base (RAG): BGE-M3 dense + sparse vectors in embedded Qdrant, merged with RRF
QDRANT_STORAGE_PATH = PROJECT_ROOT / "qdrant_data"
EMBEDDING_MODEL_ID = "BAAI/bge-m3"
EMBEDDING_DIMENSIONS = 1024
# 5, not 3: questions that mix two topics (e.g. a website and Instagram ads) need chunks for both.
RETRIEVAL_TOP_K = 5
RETRIEVAL_CANDIDATES_PER_SEARCH = 10

# Speaking: ElevenLabs. ELEVENLABS_ENABLED=false turns every ElevenLabs call off (Claude sessions must use it).
ELEVENLABS_ENABLED = os.getenv("ELEVENLABS_ENABLED", "true").strip().lower() == "true"
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_TEXT_TO_SPEECH_ENABLED = (os.getenv("ELEVENLABS_TEXT_TO_SPEECH_ENABLED") or "true").strip().lower() == "true"
# "or" keeps the default when .env has the key with an empty value.
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID") or "JBFqnCBsd6RMkjVDRZzb"
ELEVENLABS_VOICE_ID_PL = os.getenv("ELEVENLABS_VOICE_ID_PL") or ELEVENLABS_VOICE_ID
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL") or "eleven_flash_v2_5"


@dataclass(frozen=True)
class VoiceChoice:
    """One voice the pages offer. The page only ever sees the key and the label; the ElevenLabs id stays here."""

    key: str
    label: str
    voice_id: str


def read_voice_choices():
    """ELEVENLABS_VOICE_IDS in .env: comma-separated ElevenLabs voice ids, shown as Voice 1, Voice 2, ... in that
    order; the first is the default. Empty = no voice picker, each language keeps its own voice."""
    voice_ids = []
    for voice_id in os.getenv("ELEVENLABS_VOICE_IDS", "").split(","):
        if voice_id.strip() and voice_id.strip() not in voice_ids:
            voice_ids.append(voice_id.strip())
    choices = []
    for position, voice_id in enumerate(voice_ids, start=1):
        choices.append(VoiceChoice(f"voice-{position}", f"Voice {position}", voice_id))
    return choices


VOICE_CHOICES = read_voice_choices()


def find_voice_choice(voice_key):
    """None for an empty or unknown key, or when no voices are configured; the language's own voice is used then."""
    for choice in VOICE_CHOICES:
        if choice.key == voice_key:
            return choice
    return None


@dataclass(frozen=True)
class LanguageSettings:
    """Everything that differs per conversation language. The pipeline code is shared; each language gets its own
    knowledge file, Qdrant collection, system prompt, speech models and retrieval thresholds."""

    code: str
    name: str
    greeting: str
    no_passages_text: str
    knowledge_base_path: Path
    core_facts_path: Path
    knowledge_collection_name: str
    system_prompt_path: Path
    retrieval_min_dense_score: float
    retrieval_min_current_message_score: float
    speech_recognition_model: str
    speech_recognition_language: str
    elevenlabs_voice_id: str
    elevenlabs_language_code: str
    min_end_of_turn_delay_seconds: float


ENGLISH = LanguageSettings(
    code="en",
    name="English",
    greeting="Hi! How can I help you?",
    no_passages_text="(No detail passage matches this message. Answer from the core facts if they cover it. If it is a greeting, thanks, small talk, a family message or an answer to your question, reply naturally. If it asks about Smart4U and the core facts don't cover it, don't guess: say it needs to be confirmed with our team or ask a clarifying question. If it is clearly off-topic or harmful, reply with [DECLINE] only.)",
    knowledge_base_path=KNOWLEDGE_BASE_DIRECTORY / "smart4u_en.md",
    core_facts_path=KNOWLEDGE_BASE_DIRECTORY / "smart4u_core_en.md",
    knowledge_collection_name="smart4u_en",
    system_prompt_path=REASONING_STAGE_DIRECTORY / "system_prompt_en.txt",
    retrieval_min_dense_score=0.45,
    # Measured: off-topic questions ~0.35, short follow-ups ~0.43+.
    retrieval_min_current_message_score=0.40,
    speech_recognition_model=os.getenv("SPEECH_RECOGNITION_MODEL_EN") or "base.en",
    speech_recognition_language="en",
    elevenlabs_voice_id=ELEVENLABS_VOICE_ID,
    elevenlabs_language_code="en",
    # Silence after which the turn detector may end the turn, when it judges the sentence complete.
    min_end_of_turn_delay_seconds=0.4,
)

POLISH = LanguageSettings(
    code="pl",
    name="Polski",
    greeting="Cześć! W czym mogę Ci pomóc?",
    no_passages_text="(Żaden szczegółowy fragment nie pasuje do tej wiadomości. Odpowiedz na podstawie kluczowych faktów, jeśli to obejmują. Jeśli to powitanie, podziękowanie, luźna rozmowa, wiadomość od rodziny albo odpowiedź na Twoje pytanie, odpowiedz naturalnie. Jeśli pytanie dotyczy Smart4U, a kluczowe fakty tego nie obejmują, nie zgaduj: powiedz, że trzeba to potwierdzić z naszym zespołem, albo dopytaj. Jeśli wiadomość wyraźnie nie dotyczy naszych usług lub jest szkodliwa, odpowiedz samym [DECLINE].)",
    knowledge_base_path=KNOWLEDGE_BASE_DIRECTORY / "smart4u_pl.md",
    core_facts_path=KNOWLEDGE_BASE_DIRECTORY / "smart4u_core_pl.md",
    knowledge_collection_name="smart4u_pl",
    system_prompt_path=REASONING_STAGE_DIRECTORY / "system_prompt_pl.txt",
    # Measured with retrieval-test: off-topic questions <=0.39, short follow-ups ~0.41+.
    retrieval_min_dense_score=0.45,
    retrieval_min_current_message_score=0.40,
    # "base.en" is English-only; Polish needs a multilingual model, "small" or larger.
    speech_recognition_model=os.getenv("SPEECH_RECOGNITION_MODEL_PL") or "small",
    speech_recognition_language="pl",
    elevenlabs_voice_id=ELEVENLABS_VOICE_ID_PL,
    elevenlabs_language_code="pl",
    # LiveKit's turn detector has no Polish, so silence alone ends the turn: longer, or mid-sentence pauses cut in.
    min_end_of_turn_delay_seconds=0.8,
)

LANGUAGES = {ENGLISH.code: ENGLISH, POLISH.code: POLISH}
# Used where no language is picked: the terminal chat without --language, and the evaluation scripts.
DEFAULT_LANGUAGE_CODE = os.getenv("DEFAULT_LANGUAGE_CODE") or ENGLISH.code


# Fixed replies the LLM can only trigger with a tag ([DECLINE]), never word itself. Contact steps are worded by the
# LLM, so they fit the conversation. Order is priority: when a reply carries several tags, the first one here wins.
FIXED_MESSAGE_KINDS = ["decline"]

# Messages (visitor and avatar together) sent to the LLM each turn: enough for follow-ups, capped for cost and latency.
CONVERSATION_HISTORY_MAX_MESSAGES = 20


class ClientProfile:
    """The SCARD profile the avatar sits on: its contact buttons and the fixed messages per language.
    Buttons and wording differ between clients, so they live in a JSON file per client."""

    def __init__(self, contact_buttons, fixed_messages):
        self.contact_buttons = contact_buttons
        self.fixed_messages = fixed_messages

    @classmethod
    def load(cls, profile_path):
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        for message_kind in FIXED_MESSAGE_KINDS:
            for language_code in LANGUAGES:
                if not profile["fixed_messages"].get(message_kind, {}).get(language_code):
                    raise ValueError(f"{profile_path} has no '{message_kind}' fixed message for '{language_code}'.")
        return cls(profile["contact_buttons"], profile["fixed_messages"])

    def fixed_message(self, message_kind, language_code):
        return self.fixed_messages[message_kind][language_code]

    def contact_button_labels(self, language_code):
        labels = []
        for button in self.contact_buttons:
            labels.append(button["label"][language_code])
        return labels


CLIENT_PROFILE_PATH = PROJECT_ROOT / (os.getenv("CLIENT_PROFILE_PATH") or "profiles/smart4u.json")
CLIENT_PROFILE = ClientProfile.load(CLIENT_PROFILE_PATH)

# Avatar: Ditto TalkingHead on the GPU (the avatar service). Off, busy or unreachable = calls are voice only.
AVATAR_ENABLED = (os.getenv("AVATAR_ENABLED") or "false").strip().lower() == "true"
# On the pod: the local avatar service. From the PC: wss://<pod-id>-8080.proxy.runpod.net
AVATAR_SERVICE_URL = (os.getenv("AVATAR_SERVICE_URL") or "ws://127.0.0.1:8090").rstrip("/")
AVATAR_SERVICE_API_KEY = os.getenv("AVATAR_SERVICE_API_KEY", "")
# The avatar's face: uploaded to the avatar service on the first call (it animates this photo), and shown on the
# pages until the video starts and on voice-only calls. One photo, so the page and the video always match.
AVATAR_PORTRAIT_PATH = PROJECT_ROOT / (os.getenv("AVATAR_PORTRAIT_PATH") or "assets/avatar_portrait.jpg")
# Ditto's audio model hears 16 kHz mono; ElevenLabs is asked for exactly that, so the voice is never converted.
AVATAR_SAMPLE_RATE = 16000
AVATAR_ELEVENLABS_OUTPUT_FORMAT = "pcm_16000"
AVATAR_FRAME_RATE = 25
AVATAR_AUDIO_SAMPLES_PER_FRAME = AVATAR_SAMPLE_RATE // AVATAR_FRAME_RATE
# The avatar's audio and video tracks share this stream name, which makes the browser play them in sync.
AVATAR_TRACK_STREAM_NAME = "avatar"
# A talking head at ~960 px and 25 fps; caps the call's video upload.
AVATAR_VIDEO_MAX_BITRATE = 2_500_000
# Protocol 2: every frame says which answer its audio belongs to (see the avatar service's server.py).
AVATAR_STREAM_PROTOCOL = 2
AVATAR_CONNECT_TIMEOUT_SECONDS = 30
# A new call waits this long for the previous call's avatar stream to close (LiveKit ends a left call ~20 s later).
AVATAR_BUSY_WAIT_SECONDS = 30
# An answer counts as played this long after its expected end even if its last frame never arrives,
# so a lost frame can't leave the agent waiting forever.
AVATAR_ANSWER_END_GRACE_SECONDS = 5.0

# Test console web page (text chat + retrieval and timing view)
WEB_SERVER_HOST = os.getenv("WEB_SERVER_HOST", "127.0.0.1")
WEB_SERVER_PORT = int(os.getenv("WEB_SERVER_PORT", "8080"))
WEB_PAGE_PATH = PROJECT_ROOT / "web" / "index.html"
# The plain voice page (no debug panels), served by the same server at /voice.
VOICE_PAGE_PATH = PROJECT_ROOT / "web" / "voice.html"
WEB_MAX_SESSIONS = 50
# Empty = no login (this PC only). On the pod's public link set it: the browser asks for it once (any user name),
# so strangers with the link can't spend the OpenAI and ElevenLabs credits.
WEB_ACCESS_PASSWORD = os.getenv("WEB_ACCESS_PASSWORD", "")

# Evaluation: a larger free Groq model grades the avatar's answers, whichever model answers, so grading costs nothing.
# Pause between cases keeps under Groq's free per-minute token cap.
EVALUATION_JUDGE_MODEL = os.getenv("EVALUATION_JUDGE_MODEL") or "openai/gpt-oss-120b"
EVALUATION_JUDGE_CHOICE = LanguageModelChoice("evaluation-judge", "Evaluation judge", os.getenv("EVALUATION_JUDGE_PROVIDER") or "groq",
                                              EVALUATION_JUDGE_MODEL, "", True, 2000)
EVALUATION_PAUSE_SECONDS = 12
EVALUATION_RATE_LIMIT_RETRIES = 3
# Voice replies: the client wants 1-3 sentences and at most one question per reply.
EVALUATION_MAX_REPLY_SENTENCES = 3
EVALUATION_MAX_REPLY_QUESTIONS = 1

# MVP targets, used by evaluation and the test console
TARGET_REPLY_START_SECONDS = 1.2
TARGET_INTERRUPT_STOP_SECONDS = 0.3
