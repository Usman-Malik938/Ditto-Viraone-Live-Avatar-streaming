# Avatar Chatbot — notes for Claude sessions

## IMPORTANT: never call the ElevenLabs API yourself

- Claude must **not** run, test or trigger anything that calls ElevenLabs (text-to-speech, speech-to-text, voice cloning). The user tests every ElevenLabs part manually.
- Every process Claude starts must run with `ELEVENLABS_ENABLED=false`, e.g. `ELEVENLABS_ENABLED=false .venv/Scripts/python main.py web`. The speaking stage then skips audio and says so.
- Hand ElevenLabs testing to the user with the exact command to run instead.

## Other project rules

- Plan: [plan.md](plan.md). Progress tracker: https://claude.ai/artifact/5BKRkfAsSb9eProdsJAYcC
- Only one of `web`, `chat` or `reasoning-test` can run at a time (they share the local Qdrant folder).
