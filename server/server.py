"""ViraOne avatar streaming server (FastAPI) for the GPU pod.

Endpoints
  GET  /health                      model status (no auth)
  GET  /                            browser test viewer (enter the API key on the page)
  POST /v1/avatars                  upload a portrait -> {"avatar_id"}
  GET  /v1/avatars                  list portraits
  WS   /v1/stream?token=..&avatar_id=..[&protocol=2&steps=10&overlap=70&out_height=960&jpeg=80]
        the approved look (mouth, head, expression, pauses) is fixed; these only trade speed for quality:
        defaults = the tested real-time setup (1.35 s speech-to-lips); notebook offline values: steps=50&overlap=10
        client -> server: binary = speech audio, PCM16 mono 16 kHz (any chunk size, as fast as you have it)
                          text   = {"type": "segment_end"} | {"type": "interrupt"} | {"type": "stats"} | {"type": "stop"}
                                   segment_end closes the current answer, interrupt drops its queued speech and closes it
        server -> client: text   = {"type": "ready", protocol, width, height, fps, sample_rate} | {"type": "stats", ...}
                          binary = one video frame with its 640 PCM16 samples:
                            protocol 1 (default): b"F" + uint32 frame_idx + uint32 jpeg_len + jpeg + audio
                            protocol 2: b"V" + uint32 frame_idx + uint32 answer + uint32 jpeg_len + jpeg + audio
                                        answer = 1, 2, ... in the order answers are sent; 0 = idle or interrupted (silent)
  The avatar idles (silence) when no speech is queued; frames come at a steady 25 fps.

Run:  VIRAONE_API_KEY=... python server/server.py   (listens on 0.0.0.0:8080; HOST / PORT change it)
"""
import asyncio
import json
import os
import struct
import sys
import time
import uuid
from contextlib import asynccontextmanager

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
DITTO_DIR = os.environ.get("DITTO_DIR", os.path.dirname(SERVER_DIR))    # Ditto's code is the repo root
CKPT = os.environ.get("DITTO_CKPT", f"{DITTO_DIR}/checkpoints")
DATA_ROOT = os.environ.get("DITTO_DATA_ROOT", f"{CKPT}/ditto_trt_Ampere_Plus")
CFG_PKL = os.environ.get("DITTO_CFG", f"{CKPT}/ditto_cfg/v0.4_hubert_cfg_trt_online.pkl")
AVATAR_DIR = os.environ.get("AVATAR_DIR", "/workspace/avatars")
API_KEY = os.environ.get("VIRAONE_API_KEY", "")
sys.path.insert(0, DITTO_DIR)
sys.path.insert(0, SERVER_DIR)
os.chdir(DITTO_DIR)

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from avatar_stream import AvatarSDK, StreamSession, SR, FPS, OUT_HEIGHT, JPEG_QUALITY  # noqa: E402

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
PROTOCOLS = (1, 2)      # 2 adds answer numbers to frames (used by the ai_avatar voice agent)
state = {"sdk": None, "load_s": None, "busy": asyncio.Lock()}


@asynccontextmanager
async def lifespan(app):
    if not API_KEY:
        raise RuntimeError("Set VIRAONE_API_KEY: the pod's web link is public.")
    os.makedirs(AVATAR_DIR, exist_ok=True)
    t0 = time.time()
    state["sdk"] = await asyncio.to_thread(AvatarSDK, CFG_PKL, DATA_ROOT)
    state["load_s"] = round(time.time() - t0, 1)
    print(f"Ditto loaded in {state['load_s']} s ({CFG_PKL})", flush=True)
    yield


app = FastAPI(title="ViraOne avatar stream", lifespan=lifespan)


def _check(key):
    if not key or key != API_KEY:
        raise HTTPException(401, "invalid API key")


def _avatar_path(avatar_id):
    for ext in IMG_EXT:
        p = os.path.join(AVATAR_DIR, avatar_id + ext)
        if os.path.isfile(p):
            return p
    return None


@app.get("/health")
async def health():
    return {"ok": state["sdk"] is not None, "model_load_s": state["load_s"], "busy": state["busy"].locked(),
            "cfg": os.path.basename(CFG_PKL), "fps": FPS}


@app.get("/")
async def viewer():
    return FileResponse(os.path.join(SERVER_DIR, "viewer.html"))


@app.post("/v1/avatars")
async def upload_avatar(file: UploadFile = File(...), x_api_key: str = Header(None)):
    _check(x_api_key)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in IMG_EXT:
        raise HTTPException(400, f"image must be one of {IMG_EXT}")
    avatar_id = uuid.uuid4().hex[:10]
    with open(os.path.join(AVATAR_DIR, avatar_id + ext), "wb") as f:
        f.write(await file.read())
    return {"avatar_id": avatar_id}


@app.get("/v1/avatars")
async def list_avatars(x_api_key: str = Header(None)):
    _check(x_api_key)
    return {"avatars": sorted(os.path.splitext(f)[0] for f in os.listdir(AVATAR_DIR) if f.lower().endswith(IMG_EXT))}


@app.websocket("/v1/stream")
async def stream(ws: WebSocket, token: str = "", avatar_id: str = "", out_height: int = OUT_HEIGHT, steps: int = 0,
                 overlap: int = 0, jpeg: int = JPEG_QUALITY, protocol: int = 1):
    await ws.accept()
    if protocol not in PROTOCOLS:
        await ws.close(code=4400, reason=f"protocol must be one of {PROTOCOLS}")
        return
    if token != API_KEY:
        await ws.close(code=4401, reason="invalid API key")
        return
    path = _avatar_path(avatar_id)
    if path is None:
        await ws.close(code=4404, reason="unknown avatar_id")
        return
    if state["busy"].locked():
        await ws.close(code=4409, reason="another stream is running (one GPU, one stream)")
        return

    async with state["busy"]:
        loop = asyncio.get_running_loop()
        frames: asyncio.Queue = asyncio.Queue(maxsize=250)

        def on_frame(fid, answer, jpg, pcm16):  # worker thread -> event loop
            if protocol == 2:
                msg = b"V" + struct.pack("<III", fid, answer, len(jpg)) + jpg + pcm16
            else:
                msg = b"F" + struct.pack("<II", fid, len(jpg)) + jpg + pcm16

            def put():
                if frames.full():               # client too slow: drop the oldest frame, keep real time
                    frames.get_nowait()
                frames.put_nowait(msg)
            loop.call_soon_threadsafe(put)

        t0 = time.time()
        session = await asyncio.to_thread(StreamSession, state["sdk"], path, on_frame, out_height,
                                          min(max(jpeg, 50), 100), steps or None, overlap or None)
        session.start()
        w, h = session.out_size
        await ws.send_text(json.dumps({"type": "ready", "protocol": protocol, "width": w, "height": h, "fps": FPS, "sample_rate": SR,
                                       "setup_s": round(time.time() - t0, 2), **session.settings}))

        async def sender():
            while True:
                await ws.send_bytes(await frames.get())

        send_task = asyncio.create_task(sender())
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    session.push_speech(msg["bytes"])
                elif msg.get("text"):
                    cmd = json.loads(msg["text"]).get("type")
                    if cmd == "segment_end":
                        session.end_answer()
                    elif cmd == "interrupt":
                        session.interrupt()
                    elif cmd == "stats":
                        await ws.send_text(json.dumps({"type": "stats", **session.latency_report()}))
                    elif cmd == "stop":
                        break
                if session.sdk.worker_exception is not None:
                    raise session.sdk.worker_exception
        except WebSocketDisconnect:
            pass
        finally:
            send_task.cancel()
            report = session.latency_report()
            await asyncio.to_thread(session.close)
            print("stream closed:", report, flush=True)
            try:
                await ws.close()
            except Exception:
                pass


if __name__ == "__main__":
    import uvicorn
    # HOST=127.0.0.1 when the voice agent runs on the same pod and the avatar needs no public link
    # access_log off: the stream URL carries the API key (?token=), which must not end up in the log files
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", 8080)), ws_max_size=16 * 1024 * 1024,
                access_log=False)
