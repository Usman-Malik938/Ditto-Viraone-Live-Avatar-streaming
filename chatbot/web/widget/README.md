# ViraOne avatar widget for SCARD

The widget replaces the SCARD page's static circular profile picture with the live avatar. The visitor taps the circle and talks:

1. The visitor speaks.
2. The avatar answers from the Smart4U knowledge base.
3. It speaks in the chosen ElevenLabs voice, with its face animated in sync inside the same circle.

It works on current desktop Chrome and Edge, iPhone Safari and Android Chrome. The video plays inline, without going fullscreen. Sound starts with the visitor's tap; if a phone still blocks it, a "Dotknij, aby włączyć dźwięk" button appears.

Everything is in one file: [viraone-avatar-widget.html](viraone-avatar-widget.html). It holds a `<div>`, a `<style>` block and a `<script>` block, and needs no build step or dependencies; it loads `livekit-client` from jsDelivr itself.

All the AI runs on the avatar server (the RunPod GPU pod). The widget only shows the circle and streams the visitor's voice there and the avatar back. No API keys are in the widget.

## What the visitor sees

- **The circle:** the avatar's photo, then the live face during a call. A coloured ring shows the state: blue = connecting or thinking, grey-green = listening, teal = speaking.
- **Mic badge** (bottom right): tap the circle to start; during a call the badge turns red, and a tap ends the call.
- **Voice badge** (bottom left, "1", "2", "3"): picks Głos 1, 2 or 3, also during a call.
- **Short messages** float under the circle for a few seconds (errors, call ended, time left), so the page around it doesn't move.

## Settings on the div

| Attribute | Value |
|---|---|
| `data-server` | The avatar server's HTTPS address, for example `https://<pod-id>-8080.proxy.runpod.net` |
| `data-key` | The widget's key (`WIDGET_ACCESS_KEY` in the server's `.env`; `start.sh` prints it) |
| `data-language` | `pl` (default) or `en` |
| `data-size` | The circle's diameter: the old profile picture's size, for example `160px`, or `100%` to fill its box. Default `220px`. |
| `data-layout` | `circle` (default): only the circle with its two badges, a drop-in for the profile picture. `full`: the circle with a Talk button, a voice list and a status line below it. |
| `data-timing` | `true` shows the response-time panel under the avatar (see below). |

On the server, add the SCARD site to `.env`, then restart the server:

```
WIDGET_ALLOWED_ORIGINS=https://your-scard-site.example
```

`*` allows any site. It's fine for testing, but list the real SCARD address for the client.

## Replacing the profile picture

Plain HTML: replace the `<img>` of the profile picture with the whole file, then set `data-server`, `data-key` and `data-size` (the picture's size).

## React / Vite (the SCARD page)

React doesn't run `<script>` tags placed inside components, so split the file into two parts:

1. **Paste the `<style>` and `<script>` blocks into `index.html`**, just before `</body>`.
2. **Render the div** where the profile picture was, and mount it once it's in the page:

```jsx
import { useEffect, useRef } from "react";

export function ViraOneAvatar() {
  const ref = useRef(null);
  useEffect(() => {
    window.ViraOneAvatar?.mount(ref.current);
  }, []);
  return (
    <div
      ref={ref}
      data-viraone-avatar
      data-server="https://<pod-id>-8080.proxy.runpod.net"
      data-key="<access key>"
      data-language="pl"
      data-size="160px"
    />
  );
}
```

`mount()` is safe to call more than once. A div already in the page when the script loads is mounted automatically, whether it has `id="viraone-avatar"` or `data-viraone-avatar`.

## Measuring the response time on the SCARD page

Add `?viraone-timing` to the SCARD page's address, for example `https://your-scard-site.example/?viraone-timing`. Adding `#viraone-timing` works too. No code change is needed. A panel then appears under the avatar. After each answer it shows:

- **the total:** from the moment the visitor stops speaking to the avatar's first animated frame;
- **each step:**
  - end-of-speech detection;
  - knowledge base search;
  - language model (OpenAI);
  - voice (ElevenLabs);
  - avatar face (Ditto);
- **the average** over the answers in the call.

Visitors who open the page without `?viraone-timing` don't see the panel. To show it always, set `data-timing="true"` on the div.

## Session safeguards

The server enforces these. They are set in its `.env` without code changes, and `0` turns a limit off:

| Setting | Default | What it does |
|---|---|---|
| `SESSION_MAX_SECONDS` | 180 | A call ends after 3 minutes. The widget shows the time left in the last 30 seconds, and the avatar says a short goodbye. |
| `SESSION_INACTIVITY_SECONDS` | 30 | A call ends after 30 seconds in which nobody speaks. |
| `SESSIONS_PER_VISITOR_PER_HOUR` | 3 | Calls per visitor per hour. Only calls that actually connected count. |
| One active call per visitor | always | A new call from the same visitor (for example a second tab) ends the previous one. |
| `SESSIONS_PER_IP_PER_HOUR` | 10 | Call attempts per IP address per hour, a backstop for a visitor who clears the browser's storage. |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | 60 | Requests of any kind per IP address per minute. |

The visitor token is a random id the widget keeps in the browser's storage. When a call ends, the server closes it completely, so speech-to-text, the language model, ElevenLabs and the avatar stop for that call. The goodbye lines are in `profiles/smart4u.json` under `session_messages`.

## Things to know

- **HTTPS is required:** browsers allow the microphone only on HTTPS pages, both the SCARD page and the avatar server.
- **The widget key is visible in the page source, by design.** It only lets the widget start avatar calls, within the limits above. It doesn't open the server's test console or anything else, and the server's OpenAI and ElevenLabs keys never leave the server. This is baseline protection, not production-grade anti-abuse. Change `WIDGET_ACCESS_KEY` in the server's `.env` after the review, and the old snippet stops working.
- **The server must be running:** the RunPod pod during the agreed review hours. When it's off, the widget says the avatar is unavailable.
- **One call at a time on the GPU:** a second visitor during a call waits up to 30 seconds, then gets a voice-only call.
- **The styles are scoped:** every class starts with `viraone-`, so it doesn't affect the SCARD page's own styles. To change colours, override the `--viraone-*` variables on `.viraone-widget`.
- **Try it without SCARD:** open `https://<pod-id>-8080.proxy.runpod.net/widget?access=<widget key>` to see the same snippet on a page of its own. Add `&viraone-timing` for the timing panel.
