# Avatar Chatbot MVP Plan

## Goal
- A user talks by voice to an AI avatar that answers with natural speech, synced lips, head movement and facial expressions.
- Low latency, and the user can interrupt the avatar at any time.

## Language
- Build in English first, then switch to Polish
- Polish STT: test self-hosted first; fall back to ElevenLabs STT

## Stack
- **Hosting:** one monorepo on one RunPod RTX 4090 pod runs everything: web page, voice agent, LiveKit server, avatar
- **Framework:** LiveKit Agents + self-hosted LiveKit server on the pod (calls over TCP; RunPod doesn't pass UDP). Fallback: LiveKit Cloud free tier
- **VAD:** Silero
- **STT:** ElevenLabs Scribe v2 realtime; self-hosted Nemotron later, on the GPU
- **End of turn:** LiveKit turn detector (open weights, CPU); Polish ends on silence
- **LLM:** OpenAI gpt-4.1-mini (2.5 s average to voice, measured 2026-11-07); Groq gpt-oss-20b as the free fallback
- **TTS:** ElevenLabs Flash (WebSocket streaming)
- **Avatar:** Ditto TalkingHead on the same GPU (TensorRT, 25 fps, 1.35 s speech-to-lips measured on the 4090)
- **Frontend:** web page using the LiveKit browser SDK

## How it works
- User speaks → VAD + Scribe transcribe it, and the turn detector decides when they've finished → knowledge search + LLM reply (streaming) → ElevenLabs speaks → Ditto animates the face → audio and video go back to the user together.
- If the user interrupts: stop the LLM, the TTS and the avatar immediately.

```
          +------------------+
          |     browser      |
          +------------------+
               |        ^
   speech, TCP |        | video + audio, in sync (TCP)
               v        |
          +------------------+
          |  LiveKit server  |   (on the pod)
          +------------------+
               |        ^
               v        |
          +------------------+       reply text      +------------------+
          |   voice agent    | --------------------> |    ElevenLabs    |
          |   (on the pod)   | <-------------------- |   (streaming)    |
          +------------------+       voice chunks    +------------------+
               |        ^
  voice 16 kHz |        | frames + their audio, 25 fps
               v        |
          +------------------+
          |      Ditto       |   (on the pod, local WebSocket)
          +------------------+
```

## Steps

### 1. Setup
- LiveKit Cloud project and keys
- Groq and ElevenLabs keys
- Rent a GPU (RTX 4090 / L40S); the whole repo runs on it
- Repo skeleton, config, Python environment

### 2. Voice agent (no avatar) — current focus
- `python main.py web`: test console + voice page + agent + local LiveKit server (`livekit-server --dev`, free, no account)
- Silero VAD → ElevenLabs Scribe realtime STT → turn detector → Groq + RAG → ElevenLabs Flash (WebSocket streaming)
- Interruptions: VAD-based, false alarms resume, history cut to what the user heard
- Bell tone when the turn passes (high = user's turn, low = agent's turn)
- Polish: LiveKit's turn detector has no Polish, so Polish ends turns on silence (0.8 s)
- Later, on the GPU: swap Scribe for self-hosted Nemotron
- Tune end-of-turn detection and interruptions
- Target: voice reply in under 0.8 s

### 3. Calls over TCP on RunPod — go / no-go first (≈ ½ day)
- On the pod: expose one TCP port with the same number inside and outside, start `livekit-server` with TCP only and the pod's public IP
- From a laptop: join a call through the voice page
- **Works:** continue with everything on the pod. **Fails:** LiveKit Cloud free tier (3 `.env` values, no code change)

### 4. Monorepo
- Bring the Ditto repo in as `avatar_service/` (git merge done by the user); Ditto upstream code stays unchanged
- Ditto keeps its own Python 3.10 venv (TensorRT 8.6 needs it); the chatbot keeps its own; they talk over `ws://127.0.0.1`
- Ditto server additions: a `segment_end` → `segment_done` marker, so the agent knows when a reply has finished playing; listen on localhost only

### 5. Avatar bridge (built on the PC, against the running pod)
- `worker/stages/avatar/`: replace the FlashHead stubs with a Ditto bridge behind `handler.py`
- Uses LiveKit's built-in avatar parts: the agent's voice goes to a 16 kHz queue instead of the room, the bridge sends it to Ditto, LiveKit's `AvatarRunner` publishes the returned frames + audio in sync
- Interruption: LiveKit's clear → Ditto `interrupt`; the queued frames are dropped
- `AVATAR_ENABLED` + Ditto address in `config.py` / `.env`; avatar off, busy or unreachable → the call carries on voice only
- Console and voice page show the video in the avatar strip; inspector adds "first frame" timing

### 6. Deploy on the pod
- One setup script (both venvs, CUDA torch for BGE-M3, Ditto engines, LiveKit server binary, knowledge index)
- One start script launching LiveKit server, Ditto and `main.py web`
- Pod ports: 8080 web page (RunPod HTTPS link, needed for the mic), 7880 LiveKit signalling (HTTPS link), the TCP port from step 3 for call media
- Knowledge search on the GPU (expected well under 0.1 s, down from ~1 s on CPU)

### 7. Measure and tune
- Speech end → first avatar frame; interruption stop time; GPU memory with Ditto + BGE-M3 together
- Interruption sensitivity ("okay", "mm-hmm", echo), shorter spoken replies, Polish voice test

### 8. Polish
- Connect RAG to the LLM step (lookup under 100 ms)
- Build the web page
- Test interruptions, background noise and long sessions
- Final check against the targets

## MVP targets
- Reply starts within ~1.2 s
- Avatar stops within ~300 ms when the user interrupts
- No visible lip drift

## Timeline (estimate)
- Steps 3–6: about 1–2 weeks

## Risks and fallbacks
- **Calls over TCP don't connect on RunPod:** LiveKit Cloud free tier
- **One Ditto stream per GPU:** a second simultaneous caller gets voice only; more callers need more GPUs
- **Interruptions:** the face may keep moving ~1 s after a barge-in (frames already in Ditto's pipeline); sound is cut at once
- **Self-hosted STT too slow or inaccurate:** Deepgram Flux ($200 free signup credit)
- **Ditto and BGE-M3 competing for the GPU:** measure in step 7
- **Groq free-tier limits:** OpenAI key
- **ElevenLabs too slow:** Cartesia

## Tracking
- Progress tracker: https://claude.ai/artifact/5BKRkfAsSb9eProdsJAYcC
