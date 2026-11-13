# Ditto ViraOne: live avatar streaming

The ViraOne live avatar: a voice chatbot (`chatbot/`) and a real-time talking-avatar backend. The avatar service turns a portrait and a live speech stream into a 25 fps avatar video, on one GPU in the cloud, such as a RunPod RTX 4090.

It's built on [Ditto TalkingHead](https://github.com/antgroup/ditto-talkinghead) by Ant Group (Apache-2.0), using its real-time TensorRT pipeline (`stream_pipeline_online.py`). Ditto's original README is kept as [README_DITTO.md](README_DITTO.md). Its code is unchanged at upstream commit `c3e47ee`, and everything ViraOne-specific lives in `server/`, `client/`, `deploy/` and `chatbot/`.

## How it works

```
local PC (your app, LLM + TTS)                GPU pod (this repo)
  speech audio, PCM16 16 kHz   ── WebSocket ─▶  Ditto online: HuBERT → motion → render (TensorRT)
  plays frames + audio         ◀─ WebSocket ──  JPEG frame + its 40 ms of audio, 25 fps
```

- **One WebSocket per conversation.** The avatar is always live: when no speech is queued it's fed silence, so it idles (blinks, subtle motion), and speech starts on the next 0.2 s step.
- **Send speech as fast as you get it,** for example from ElevenLabs with `output_format=pcm_16000`. The server plays it out in real time.
- **Every frame carries its own audio,** so the player keeps lips and sound in sync.
- **Final ViraOne settings, the same for every portrait:** exactly the v6 FINAL Colab notebook values (the `FINAL GLOBAL SETTINGS` block in `server/avatar_stream.py`):
  - mouth 0.9, smoothing 3, Neutral emotion, own-face reference;
  - head 0.6 / 0.6 / 0.5;
  - seed 1024, max size 1280, crop 2.3;
  - lips close in pauses: the notebook's `pause_alpha` formula on the lip points only;
  - the head-drift stabiliser.
- **Real-time speed, chosen separately for latency:** 10 motion steps and overlap 70 (Ditto's real-time values; 0.4 s motion blocks). The tested result is **1.35 s** from speech sent to the first speaking frame on the RTX 4090.

## Run the whole pipeline: chatbot + avatar on one RunPod pod

`chatbot/` is the voice chatbot: knowledge base, LLM (OpenAI), ElevenLabs speech-to-text and voice, and the LiveKit
voice agent (see [chatbot/README.md](chatbot/README.md)). It sends the agent's voice to this avatar service over
`ws://127.0.0.1:8090` (protocol 2), and LiveKit sends the face and voice to the browser together.

```
browser ──HTTPS──▶ test page (8080) ──▶ LiveKit token
browser ◀──WebRTC over one TCP port──▶ LiveKit server (signalling: HTTPS 7880)
                                          ▲
voice agent: VAD + Scribe → knowledge base + gpt-4.1-mini → ElevenLabs (pcm_16000) → Ditto (127.0.0.1:8090) → frames + audio
```

RunPod passes no UDP, so calls send their audio and video over **one TCP port** with the same number inside and
outside the pod (RunPod's port `70000` setting).

**Pod:** RTX 4090, RunPod PyTorch template, 50 GB container disk, a 60 GB volume on `/workspace`, and these ports:

| Port | Type | For |
|---|---|---|
| 8080 | HTTP | test console (`/`) and voice page (`/voice`) |
| 7880 | HTTP | LiveKit signalling |
| 70000 | TCP | call audio and video |
| 22 | TCP | SSH |

**On the pod, once (about 20 minutes):**

```
cd /workspace && git clone https://github.com/Usman-Malik938/Ditto-Viraone-Live-Avatar-streaming.git
bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/setup.sh
```

**Add your keys** (the input is hidden; the keys go to `chatbot/.env`):

```
bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/set_key.sh OPENAI_API_KEY
bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/set_key.sh ELEVENLABS_API_KEY
```

`set_key.sh ELEVENLABS_VOICE_IDS` sets the voice picker's voices: ElevenLabs voice ids, comma-separated, the first one
the default. The pages show them only as Voice 1, 2, 3.

**Add the portrait:** copy the approved selfie to `chatbot/assets/avatar_portrait.jpg` (git-ignored).

**Start or restart everything:**

```
bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/runpod/start.sh
```

It prints the test link: `https://<pod-id>-8080.proxy.runpod.net`. The browser asks for `WEB_ACCESS_PASSWORD` from `chatbot/.env` (any user name). Logs are in `/workspace/logs/`.

**SCARD widget:** the client's page embeds [chatbot/web/widget/viraone-avatar-widget.html](chatbot/web/widget/viraone-avatar-widget.html)
in place of its circular profile picture; see [its README](chatbot/web/widget/README.md). Try it at
`https://<pod-id>-8080.proxy.runpod.net/widget?access=<WEB_ACCESS_PASSWORD>` (add `&viraone-timing` for the response-time
panel). The session safeguards (3-minute calls, 30-second silence timeout, calls per visitor per hour, rate limits) are
set in `chatbot/.env`; the widget README lists them.

If calls don't connect over TCP, switch to the LiveKit Cloud free tier instead: set `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` in `chatbot/.env` (no code change).

## API

| | |
|---|---|
| `GET /health` | model status (no key needed) |
| `GET /` | browser test viewer: enter the key, upload a portrait, connect, then speak a voice file |
| `POST /v1/avatars` | upload a portrait (`multipart file`, header `x-api-key`) → `{"avatar_id"}` |
| `GET /v1/avatars` | list the uploaded portraits |
| `WS /v1/stream?token=KEY&avatar_id=ID[&protocol=2&steps=10&overlap=70&out_height=960&jpeg=80]` | the live stream; the defaults are the tested real-time setup (1.35 s speech-to-lips on the RTX 4090), and `steps=50&overlap=10` gives the notebook's offline values (slower) |

**On `/v1/stream`:**

| Direction | Message |
|---|---|
| client → server, binary | speech audio, PCM16 mono 16 kHz, any chunk size |
| client → server, text | `{"type":"segment_end"}` closes the current answer, `{"type":"interrupt"}` drops the queued speech and closes the answer (barge-in), `{"type":"stats"}` returns latency and fps, `{"type":"stop"}` ends the stream |
| server → client, text | `{"type":"ready","protocol","width","height","fps","sample_rate","setup_s"}` |
| server → client, binary, protocol 1 (default) | one frame: `b"F"` + uint32 `frame_idx` + uint32 `jpeg_len` + JPEG + 640 PCM16 samples |
| server → client, binary, `protocol=2` | one frame: `b"V"` + uint32 `frame_idx` + uint32 `answer` + uint32 `jpeg_len` + JPEG + 640 PCM16 samples |

Protocol 2 is what the [ai_avatar](https://github.com/musama020/ai_avatar) voice agent uses. Answers are numbered 1, 2, ... in the order their speech is sent, and each `segment_end` or `interrupt` closes one. A frame carries the number of the answer its first audio sample belongs to; 0 means idle. Frames of an interrupted answer that were already in the pipeline still arrive, with silent audio and answer 0.

There's one stream per GPU at a time; a second connection is refused with code 4409. The pod's web link is public, so every call needs the API key.

## Run on a RunPod GPU pod

```bash
cd /workspace && git clone https://github.com/Usman-Malik938/Ditto-Viraone-Live-Avatar-streaming.git
bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/setup_pod.sh      # once, ~10 min
VIRAONE_API_KEY=<long-random-key> bash /workspace/Ditto-Viraone-Live-Avatar-streaming/deploy/start_server.sh
```

- **Pod:** an RTX 4090 (or any Ampere-or-newer GPU) with the RunPod PyTorch template, HTTP port **8080** exposed and the repo in `/workspace`.
- **Server URL:** `https://<pod-id>-8080.proxy.runpod.net`.
- **Updating:** `git pull` on the pod, then restart the server.

## Test from your local PC

```bash
pip install -r client/requirements.txt
python client/client.py --server https://<pod-id>-8080.proxy.runpod.net --key <key> --photo selfie.jpeg --voice speech.mp3
```

The client prints the avatar setup time, the **speech-to-lips latency** and the fps, and saves `stream_test.mp4`. You can also open the server URL in a browser to use the test viewer.

## Layout

| Path | |
|---|---|
| `server/avatar_stream.py` | streaming session on top of Ditto's `StreamSDK`: pacing, idle, pause gate, head stabiliser, frame and audio sync |
| `server/server.py` | the FastAPI app |
| `server/viewer.html` | the browser test viewer |
| `client/client.py` | local test client |
| `deploy/` | avatar-only pod setup and start scripts, pinned requirements |
| `deploy/runpod/` | the whole pipeline on one pod: `setup.sh`, `set_key.sh`, `start.sh`, the `.env` template |
| `chatbot/` | the voice chatbot (knowledge base, LLM, ElevenLabs, LiveKit agent, test pages) |
| everything else | Ditto upstream (`core/`, `stream_pipeline_*.py`, `inference.py`, `scripts/`) |

## License

Ditto is Apache-2.0 (see [LICENSE](LICENSE)). The ViraOne additions are in `server/`, `client/` and `deploy/`.
