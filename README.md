# Ditto ViraOne: live avatar streaming

A real-time talking-avatar backend for ViraOne. It turns a portrait and a live speech stream into a 25 fps avatar video, on one GPU in the cloud, such as a RunPod RTX 4090.

It's built on [Ditto TalkingHead](https://github.com/antgroup/ditto-talkinghead) by Ant Group (Apache-2.0), using its real-time TensorRT pipeline (`stream_pipeline_online.py`). Ditto's original README is kept as [README_DITTO.md](README_DITTO.md). Its code is unchanged at upstream commit `c3e47ee`, and everything ViraOne-specific lives in `server/`, `client/` and `deploy/`.

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

## API

| | |
|---|---|
| `GET /health` | model status (no key needed) |
| `GET /` | browser test viewer: enter the key, upload a portrait, connect, then speak a voice file |
| `POST /v1/avatars` | upload a portrait (`multipart file`, header `x-api-key`) → `{"avatar_id"}` |
| `GET /v1/avatars` | list the uploaded portraits |
| `WS /v1/stream?token=KEY&avatar_id=ID[&steps=10&overlap=70&out_height=960&jpeg=80]` | the live stream; the defaults are the tested real-time setup (1.35 s speech-to-lips on the RTX 4090), and `steps=50&overlap=10` gives the notebook's offline values (slower) |

**On `/v1/stream`:**

| Direction | Message |
|---|---|
| client → server, binary | speech audio, PCM16 mono 16 kHz, any chunk size |
| client → server, text | `{"type":"interrupt"}` drops the queued speech (barge-in), `{"type":"stats"}` returns latency and fps, `{"type":"stop"}` ends the stream |
| server → client, text | `{"type":"ready","width","height","fps","sample_rate","setup_s"}` |
| server → client, binary | one frame: `b"F"` + uint32 `frame_idx` + uint32 `jpeg_len` + JPEG + 640 PCM16 samples |

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
| `deploy/` | pod setup script, pinned requirements and start script |
| everything else | Ditto upstream (`core/`, `stream_pipeline_*.py`, `inference.py`, `scripts/`) |

## License

Ditto is Apache-2.0 (see [LICENSE](LICENSE)). The ViraOne additions are in `server/`, `client/` and `deploy/`.
