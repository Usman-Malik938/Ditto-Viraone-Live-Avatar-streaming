# Avatar Chatbot

Real-time voice chatbot with a talking AI avatar (lip-sync, head movement, facial expression). MVP stage.

## Progress tracker

https://claude.ai/artifact/5BKRkfAsSb9eProdsJAYcC

Full plan: [plan.md](plan.md)

## Stack

| Stage | Choice |
|---|---|
| Framework | LiveKit Agents + LiveKit server (local `--dev` now; Cloud or self-hosted later) |
| VAD | Silero |
| STT | ElevenLabs Scribe v2 realtime now; NVIDIA Nemotron Speech Streaming 0.6B once the GPU is available |
| End of turn | LiveKit turn detector (open weights, CPU); no Polish model, so Polish ends turns on silence |
| LLM | gpt-oss-20b via Groq API, free tier (fallback: OpenAI GPT if limits are hit) |
| Knowledge base | Qdrant (embedded) + BGE-M3, hybrid search with RRF |
| TTS | ElevenLabs Flash |
| Avatar | Ditto TalkingHead on our RTX 4090 (separate avatar service, 25 fps, ~1.35 s speech-to-lips) |

## Project layout

```
main.py                   one entry point, picks which process to run
worker/config.py          every setting; secrets come from .env
worker/handler.py         wires the stages into one agent session
worker/livekit/           LiveKit agent class, room tokens, reply details, local LiveKit server starter
worker/web/               console server (typed chat, call tokens) and the 'web' launcher
worker/stages/listening/  Silero VAD, Scribe/local STT, turn handling (Nemotron later)
worker/stages/reasoning/  Groq/OpenAI LLM, system prompt, RAG hook
worker/stages/speaking/   ElevenLabs TTS, turn bell
worker/stages/avatar/     Ditto bridge: sends the agent's voice to the avatar service, publishes its video + audio
evaluation/               answer quality and retrieval tests
web/                      test console (index.html) and voice page (voice.html)
assets/                   avatar portrait shown before the video starts (not committed)
```

Each stage is self-contained. Other code calls a stage only through its `handler.py`.

## Running

Copy `.env.example` to `.env` and fill in the keys.

Text-only test of the knowledge base + LLM:

```
python main.py index-knowledge
python main.py web
python main.py chat --language pl
python main.py reasoning-test
```

`web` opens the test console at http://localhost:8080 and the plain voice page at http://localhost:8080/voice (see "Voice" below). Type a question, or press **Start talking** to join a voice call: the voice agent answers from the knowledge base and speaks with ElevenLabs. Each reply shows how long every step took and which knowledge was used. Typing during a call sends the message into the call. Set `ELEVENLABS_ENABLED=false` in `.env` to keep every ElevenLabs call off (speech-to-text runs locally and replies are text only). The model picker switches between the free Groq models and paid OpenAI models (enabled only when `OPENAI_API_KEY` is set; the page always opens on the free default, and switching starts a new conversation). The language picker in the header switches between English and Polish; each language has its own core facts file (`knowledge_base/smart4u_core_<code>.md`: prices, contact details, delivery times, service scope and portfolio, sent with every message), detail knowledge file searched per message (`knowledge_base/smart4u_<code>.md`; lines starting with `> ` are search phrases, never shown to the model), Qdrant collection, system prompt (`system_prompt_<code>.txt`) and speech-to-text model, all set in `LANGUAGES` in `worker/config.py`. Re-run `index-knowledge` after editing a knowledge file.

The avatar acts as a sales consultant: short answers, one follow-up question, approved prices and delivery times only when the visitor's need matches them, and a clear contact step (phone, email, WhatsApp) once the visitor is ready. Anything out of scope (off-topic, harmful or manipulative requests, and also self-harm, violence and emergencies) makes the LLM reply with `[DECLINE]`; the pipeline removes the tag and speaks the profile's fixed stop message instead, so the wording never changes. The contact buttons and the stop message come from a per-client file (`profiles/<client>.json`, chosen with `CLIENT_PROFILE_PATH`). Each turn sends the last `CONVERSATION_HISTORY_MAX_MESSAGES` messages to the LLM. Only one of `web`, `agent`, `chat` or `reasoning-test` can run at a time, because they share the local vector database folder.

### Voice

Voice runs through LiveKit, inside the same `python main.py web` process (the console and the agent share the
knowledge base, which only one process may open). The local LiveKit server is free and needs no account: download
`livekit_<version>_windows_amd64.zip` from https://github.com/livekit/livekit/releases and unzip it into
`tools\livekit\` (git-ignored). `web` starts it (unless one is already running; its log goes to
`tools\livekit\livekit-server.log`), then the console, then the agent, and prints a Ready line. Ctrl+C stops all of
it. Without the server binary the console still starts, text only.

The agent listens with Silero VAD and Scribe realtime, answers from the same knowledge base and prompt as typed chat,
and speaks with ElevenLabs (streamed over a WebSocket). Talking over it stops it; a short noise with no words makes it
resume. A high bell marks the user's turn, a low bell the agent's (`TURN_BELL_ENABLED`). The agent's terminal logs a
`reply timing` line per reply showing whether speech started before the LLM finished. A call started from the console
continues that tab's conversation, and typing after the call continues from what was said.

### Avatar

With `AVATAR_ENABLED=true` each call opens a stream to the Ditto avatar service (`AVATAR_SERVICE_URL`, protocol 2).
The agent's voice no longer goes straight into the room: ElevenLabs sends raw 16 kHz audio (`pcm_16000`, the format
Ditto hears), the avatar stage forwards each chunk to Ditto as it arrives, and Ditto sends back 25 frames a second,
each carrying its own 40 ms of that audio. LiveKit's `AvatarRunner` publishes the frames and only that audio as one
synchronised pair of tracks, so lips and voice can't drift apart. On an interruption the stage tells Ditto to drop and
silence the rest of the answer. One call per GPU: when the avatar is off, busy or unreachable the call goes on voice
only and the page shows the still portrait. The portrait (`AVATAR_PORTRAIT_PATH`, default
`assets/avatar_portrait.jpg`) is the one photo for both: it is uploaded to the avatar service on the first call. The agent logs an `avatar timing` line per answer
(first face frame, and whether the face spoke while its voice was still arriving), and the console shows "Avatar face"
in each reply's timings.

Run `python main.py agent download-files` once on a new machine for the VAD and turn-detector models.
`python main.py agent console` runs only the agent, through this PC's mic and speakers, without the server or a page
(no bell in that mode).

## Run the whole pipeline on RunPod

This folder is the chatbot half of the [ViraOne live avatar repo](../README.md); the Ditto avatar service is the repo
root. Setup, keys and start for one RTX 4090 pod are in the root README ("Run the whole pipeline").

## Public access (Cloudflare tunnel)

`start.sh` builds the Docker image, starts the web chat and a Cloudflare quick tunnel, waits until the chatbot is ready and prints a public `https://….trycloudflare.com` link. No router or firewall changes are needed.

Through the tunnel the console is **text only**: voice calls need the browser to reach a LiveKit server, and the
local one is neither in the Docker image nor reachable through the tunnel. Public voice needs LiveKit Cloud (set
`LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` in `.env`).

Before the first run, copy `.env.example` to `.env` and fill in at least `GROQ_API_KEY`. The first start downloads about 1 GB of packages and 5 GB of models into `models/`, so it takes a while; later starts take a minute or two.

### Windows

Needs Docker Desktop (running) and Git for Windows, whose Git Bash runs the script. From CMD:

```
cd /d C:\projects\avatar_chatbot && "C:\Program Files\Git\bin\bash.exe" start.sh
```

Without Docker, run the local server and a tunnel in two CMD windows instead:

```
winget install --id Cloudflare.cloudflared
cd /d C:\projects\avatar_chatbot && .venv\Scripts\python main.py web
cloudflared tunnel --url http://localhost:8080
```

### Ubuntu / Debian

Install Docker once, then log out and back in so your user can run it:

```
curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker $USER
```

Start it:

```
cd ~/avatar_chatbot && chmod +x start.sh && ./start.sh
```

On a laptop that should keep serving with the lid closed:

```
sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf && sudo systemctl restart systemd-logind
```

### Everyday commands (both systems, from the project folder)

| Task | Command |
|---|---|
| Show the current link again | `./start.sh` |
| Follow the logs | `docker compose logs -f` |
| Stop everything | `docker compose down` |
| Re-index after editing a knowledge file | `docker compose run --rm chatbot python main.py index-knowledge` then `docker compose restart chatbot` |
| Apply code changes | `./start.sh` (rebuilds the image) |

On Windows CMD, replace `./start.sh` with `"C:\Program Files\Git\bin\bash.exe" start.sh`.

### Things to know

- The link changes whenever the tunnel container is recreated (reboot, `docker compose down`). A permanent link needs a free Cloudflare account and a named tunnel.
- The page has no login: anyone with the link can chat and spends the Groq quota (and ElevenLabs credits when voice is on).
- Containers restart on their own after a reboot; run `./start.sh` to print the new link.
- The container keeps its own knowledge index in a Docker volume and doesn't publish a port, so it can run next to a local `web` server.

## MVP targets

- Reply starts within ~1.2 s
- Avatar stops within ~300 ms when the user interrupts
- No visible lip drift
