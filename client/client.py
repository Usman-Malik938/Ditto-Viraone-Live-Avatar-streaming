"""Local test client for the ViraOne avatar stream (run on your Mac / PC).

Streams a voice file to the GPU server, receives the live frames, reports latency and saves an mp4.

  pip install -r client/requirements.txt
  python client.py --server https://<pod-id>-8080.proxy.runpod.net --key $VIRAONE_API_KEY \
                   --photo selfie.jpeg --voice speech.mp3 --out stream_test.mp4
"""
import argparse
import asyncio
import json
import os
import struct
import subprocess
import time
import wave

import imageio_ffmpeg
import requests
import websockets

SR, SPF = 16000, 640


def load_pcm16(path):
    """Any audio file -> PCM16 mono 16 kHz bytes (uses the ffmpeg bundled with imageio-ffmpeg)."""
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    return subprocess.run([ff, "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                          check=True, capture_output=True).stdout


async def run(a):
    base = a.server.rstrip("/")
    avatar_id = a.avatar_id
    if a.photo:
        with open(a.photo, "rb") as f:
            r = requests.post(f"{base}/v1/avatars", files={"file": (os.path.basename(a.photo), f)}, headers={"x-api-key": a.key}, timeout=60)
        r.raise_for_status()
        avatar_id = r.json()["avatar_id"]
        print("avatar_id:", avatar_id)
    pcm = load_pcm16(a.voice)
    speech_s = len(pcm) / 2 / SR
    ws_url = base.replace("https://", "wss://").replace("http://", "ws://") + f"/v1/stream?token={a.key}&avatar_id={avatar_id}"

    frames, audio, t_send, t_first_speech, t_ready = [], bytearray(), None, None, None
    t_connect = time.time()
    async with websockets.connect(ws_url, max_size=None) as ws:
        ready = json.loads(await ws.recv())
        t_ready = time.time()
        print(f"ready {ready['width']}x{ready['height']} (connect + avatar setup {t_ready - t_connect:.2f} s)")
        await asyncio.sleep(a.idle_before)             # show the idle avatar first
        speech_from = None

        async def send_speech():
            nonlocal t_send
            t_send = time.time()
            for i in range(0, len(pcm), 16000):        # 0.5 s chunks, as fast as possible (like a TTS stream)
                await ws.send(pcm[i:i + 16000])

        sender = None
        end_at = None
        while True:
            msg = await ws.recv()
            if isinstance(msg, str):
                print(msg)
                continue
            fid, n = struct.unpack("<II", msg[1:9])
            frames.append(msg[9:9 + n])
            audio += msg[9 + n:]
            if sender is None and time.time() - t_ready >= a.idle_before:
                speech_from = len(frames)
                sender = asyncio.create_task(send_speech())
            if t_first_speech is None and speech_from is not None:
                seg = msg[9 + n:]
                if seg and max(abs(v) for v in struct.unpack(f"<{len(seg) // 2}h", seg)) > 600:
                    t_first_speech = time.time()
                    end_at = t_first_speech + speech_s + a.idle_after
            if end_at and time.time() > end_at:
                await ws.send(json.dumps({"type": "stats"}))
                while True:
                    m = await ws.recv()
                    if isinstance(m, str):
                        print("server stats:", m)
                        break
                break
    dur = len(frames) / 25
    print(f"\nreceived {len(frames)} frames ({dur:.1f} s of video) in {time.time() - t_ready:.1f} s")
    if t_send and t_first_speech:
        print(f"speech-to-lips latency (first audio sent -> first speaking frame received): {t_first_speech - t_send:.2f} s")

    tmp = a.out + ".frames"
    os.makedirs(tmp, exist_ok=True)
    for i, jpg in enumerate(frames):
        with open(f"{tmp}/{i:06d}.jpg", "wb") as f:
            f.write(jpg)
    with wave.open(f"{tmp}/audio.wav", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(bytes(audio))
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", "25", "-i", f"{tmp}/%06d.jpg", "-i", f"{tmp}/audio.wav",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-c:a", "aac", "-shortest", a.out], check=True)
    print("saved", a.out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--server", required=True, help="e.g. https://<pod-id>-8080.proxy.runpod.net")
    p.add_argument("--key", default=os.environ.get("VIRAONE_API_KEY"))
    p.add_argument("--photo", help="portrait to upload (or use --avatar_id)")
    p.add_argument("--avatar_id")
    p.add_argument("--voice", required=True, help="wav / mp3 speech")
    p.add_argument("--out", default="stream_test.mp4")
    p.add_argument("--idle_before", type=float, default=2.0, help="seconds of idle avatar before speaking")
    p.add_argument("--idle_after", type=float, default=1.5)
    run_args = p.parse_args()
    if not run_args.key:
        p.error("pass --key or set VIRAONE_API_KEY")
    asyncio.run(run(run_args))
