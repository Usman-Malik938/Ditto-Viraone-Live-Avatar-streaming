"""Real-time Ditto avatar streaming: audio in, video frames out, at 25 fps.

Built on Ditto's online pipeline (stream_pipeline_online.StreamSDK, TensorRT, HuBERT chunk features).
A session keeps the avatar alive continuously: when no speech is queued it is fed silence, so the
face idles (blinks, subtle motion) and speech starts on the next 0.2 s tick.

Final global settings (same as the v6 FINAL Colab notebook), applied to every portrait:
mouth 0.9, smoothing 3, Neutral emotion, own-face reference, head 0.6 / 0.6 / 0.5,
lips close in pauses (lips-only vad_alpha), head-drift stabiliser.
"""
import math
import os
import queue
import threading
import time

import cv2
import numpy as np

import core.atomic_components.motion_stitch as _ms
from stream_pipeline_online import StreamSDK

SR = 16000
FPS = 25
SPF = SR // FPS                 # 640 audio samples per video frame
CHUNKSIZE = (3, 5, 2)           # Ditto online: 3 frames history, 5 new frames, 2 frames lookahead
TICK_FRAMES = CHUNKSIZE[1]      # 5 frames = 0.2 s per tick
TICK_SAMPLES = TICK_FRAMES * SPF
SPLIT_LEN = int(sum(CHUNKSIZE) * 0.04 * SR) + 80   # 6480 samples per HuBERT window
PREFIX = CHUNKSIZE[0] * SPF     # zeros prepended once, as in Ditto's inference.py

FINAL_STYLE = {
    "smo_k_d": 3,
    "emo": 4,                   # Neutral
    "ch_info": None,            # each photo's own face as the reference
    "use_d_keys": {"exp": 0.90, "pitch": 0.6, "yaw": 0.6, "roll": 0.5, "t": 0.6},
    "crop_scale": 2.3,
    "max_size": 1280,
}
PAUSE_CLOSE = 1.0               # lips close in pauses (0 = off)
DRIFT_WINDOW_S = 1.5            # head motion slower than this counts as drift


# ---- lips close in pauses: Ditto's vad_alpha, applied to the 6 lip points only (stock blends the whole face) ----
LIP_IDX = [6, 12, 14, 17, 19, 20]


def _ctrl_vad_lips(x_d_info, dst, alpha):
    exp = x_d_info["exp"].reshape(1, 21, 3).copy()
    src = dst["exp"].reshape(1, 21, 3)
    exp[:, LIP_IDX] = exp[:, LIP_IDX] * alpha + src[:, LIP_IDX] * (1 - alpha)
    x_d_info["exp"] = exp.reshape(x_d_info["exp"].shape).astype(np.float32)
    return x_d_info


_ms.ctrl_vad = _ctrl_vad_lips

# ---- head stabiliser: causal running average removes slow pitch/yaw/roll drift ----
if not hasattr(_ms, "_orig_ctrl_motion"):
    _ms._orig_ctrl_motion = _ms.ctrl_motion
_drift = {"on": False, "state": {}}


def _ctrl_motion_stable(x_d_info, **kw):
    if _drift["on"]:
        decay = math.exp(-1 / (FPS * DRIFT_WINDOW_S))
        for k in ("pitch", "yaw", "roll"):
            deg = float(np.ravel(_ms.bin66_to_degree(x_d_info[k]))[0])
            st = _drift["state"].setdefault(k, {"base": deg, "avg": deg})
            st["avg"] = decay * st["avg"] + (1 - decay) * deg
            kw[f"delta_{k}"] = kw.get(f"delta_{k}", 0) - (st["avg"] - st["base"])
    return _ms._orig_ctrl_motion(x_d_info, **kw)


_ms.ctrl_motion = _ctrl_motion_stable


class PauseGate:
    """Causal per-frame lip gate from loudness (rolling, no whole-clip statistics).

    Speech level is tracked with a slow running peak; a frame counts as speech when it is within
    ~25 dB of that level and above an absolute floor. Lips open ~80 ms early (2-frame lookahead,
    which Ditto's window already provides) and open/close over ~200 ms.
    """

    def __init__(self, strength=PAUSE_CLOSE, lead=2, floor_db=-50.0):
        self.strength, self.lead, self.floor = strength, lead, floor_db
        self.peak = -20.0
        self.db = []          # per-frame dB of all audio fed so far
        self.speech = []      # per-frame 0/1

    def push(self, samples):
        frames = samples.reshape(-1, SPF)
        for f in frames:
            db = 20 * math.log10(math.sqrt(float((f ** 2).mean())) + 1e-6)
            if db > self.peak:
                self.peak = db
            else:
                self.peak = max(self.peak - 0.02, -40.0)   # slow decay so a quiet speaker still registers
            self.db.append(db)
            self.speech.append(1.0 if (db > self.floor and db > self.peak - 25.0) else 0.0)

    def alpha(self, fid):
        """vad_alpha for frame fid; needs frames up to fid + lead + 2 pushed."""
        sp = self.speech
        win = [max(sp[j:j + self.lead + 1] or [0.0]) for j in range(fid - 2, fid + 3) if 0 <= j < len(sp)]
        gate = sum(win) / max(len(win), 1)
        return float(min(max(1 - self.strength * (1 - gate), 0.0), 1.0))


class AvatarSDK(StreamSDK):
    """StreamSDK whose writer hands frames to a callback instead of an mp4, with avatar registration cached."""

    def __init__(self, cfg_pkl, data_root, **kw):
        super().__init__(cfg_pkl, data_root, **kw)
        self.on_frame = None
        self._reg_cache = {}
        orig_registrar = self.avatar_registrar

        def cached_registrar(source_path, **kwargs):
            key = (source_path, os.path.getmtime(source_path), tuple(sorted((k, str(v)) for k, v in kwargs.items())))
            if key not in self._reg_cache:
                self._reg_cache[key] = orig_registrar(source_path, **kwargs)
            return self._reg_cache[key]

        self.avatar_registrar = cached_registrar

    def _writer_worker(self):
        while not self.stop_event.is_set():
            try:
                item = self.writer_queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break
            if self.on_frame is not None:
                self.on_frame(item)
            self.writer_pbar.update()


class StreamSession:
    """One continuous avatar stream. Thread-based; feed speech with push_speech(), receive frames via on_frame.

    on_frame(frame_idx, jpeg_bytes, pcm16_bytes): called from a worker thread for every 40 ms frame, with the
    640 audio samples that belong to it, so the client can play picture and sound in sync.
    """

    def __init__(self, sdk: AvatarSDK, source_path, on_frame, out_height=960, jpeg_quality=80,
                 sampling_timesteps=None, style=None):
        self.sdk, self.on_frame = sdk, on_frame
        self.out_height, self.jpeg_quality = out_height, jpeg_quality
        self.speech = bytearray()            # queued speech, float32 bytes at 16 kHz
        self.lock = threading.Lock()
        self.fed = np.zeros(PREFIX, dtype=np.float32)   # all audio fed so far, incl. the Ditto prefix
        self.fed_offset = 0                  # samples dropped from the front of self.fed
        self.next_window = 0                 # next HuBERT window index to submit
        self.out_idx = 0
        self.gate = PauseGate()
        self.ctrl_info = {}
        self.running = False
        self.stats = {"started": None, "frames": 0, "first_speech_in": None, "first_speech_frame_out": None,
                      "speech_frame_start": None, "late_ticks": 0}

        kwargs = dict(FINAL_STYLE if style is None else style)
        if sampling_timesteps:
            kwargs["sampling_timesteps"] = sampling_timesteps
        sdk.on_frame = self._emit
        _drift["on"], _drift["state"] = True, {}
        sdk.setup(source_path, "/tmp/ditto_stream_unused.mp4", online_mode=True, **kwargs)
        try:
            sdk.writer.close()               # Ditto opens an mp4 writer in setup(); frames go to the callback instead
        except Exception:
            pass
        sdk.setup_Nd(N_d=-1, fade_in=-1, fade_out=-1, ctrl_info=self.ctrl_info)
        h, w = sdk.source_info["img_rgb_lst"][0].shape[:2]
        self.out_size = (w, h) if not out_height or h <= out_height else (round(w * out_height / h / 2) * 2, out_height)

    # -- input --
    def push_speech(self, pcm16: bytes):
        a = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        with self.lock:
            if self.stats["first_speech_in"] is None and len(a):
                self.stats["first_speech_in"] = time.time()
            self.speech += a.tobytes()

    def interrupt(self):
        """Drop queued speech (barge-in). Frames already in the pipeline still play out (~latency)."""
        with self.lock:
            self.speech = bytearray()

    def queued_seconds(self):
        with self.lock:
            return len(self.speech) / 4 / SR

    # -- pacing loop: one 0.2 s tick of audio per 0.2 s of wall time --
    def start(self):
        self.running = True
        self.stats["started"] = time.time()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        next_t = time.monotonic()
        try:
            while self.running and not self.sdk.stop_event.is_set():
                with self.lock:
                    n = min(len(self.speech) // 4, TICK_SAMPLES)
                    take = np.frombuffer(bytes(self.speech[: n * 4]), dtype=np.float32)
                    del self.speech[: n * 4]
                if n and self.stats["speech_frame_start"] is None:
                    self.stats["speech_frame_start"] = (len(self.fed) - PREFIX + self.fed_offset) // SPF
                chunk = np.concatenate([take, np.zeros(TICK_SAMPLES - n, dtype=np.float32)])
                self.gate.push(chunk)
                self.fed = np.concatenate([self.fed, chunk])
                self._submit_ready_windows()
                next_t += TICK_FRAMES / FPS
                delay = next_t - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    self.stats["late_ticks"] += 1
                    if delay < -1.0:          # far behind: resync the clock instead of bursting
                        next_t = time.monotonic()
        except Exception as e:                # surfaced through close()
            self.sdk.worker_exception = e
            self.sdk.stop_event.set()

    def _submit_ready_windows(self):
        total = len(self.fed) + self.fed_offset
        while self.next_window * TICK_SAMPLES + SPLIT_LEN <= total:
            s = self.next_window * TICK_SAMPLES - self.fed_offset
            window = self.fed[s: s + SPLIT_LEN]
            f0 = self.next_window * TICK_FRAMES
            for fid in range(f0, f0 + TICK_FRAMES):
                a = self.gate.alpha(fid)
                if a < 0.999:
                    self.ctrl_info[fid] = {"vad_alpha": a}
            self.sdk.run_chunk(window, CHUNKSIZE)
            self.next_window += 1
        # keep ~10 s of audio for frame sync, drop the rest
        keep = 10 * SR
        if len(self.fed) > keep + SPLIT_LEN:
            cut = len(self.fed) - keep
            self.fed, self.fed_offset = self.fed[cut:], self.fed_offset + cut
            for fid in [f for f in self.ctrl_info if f < self.next_window * TICK_FRAMES - 50]:
                del self.ctrl_info[fid]

    # -- output --
    def _emit(self, frame_rgb):
        fid = self.out_idx
        self.out_idx += 1
        img = frame_rgb if frame_rgb.shape[1::-1] == self.out_size else cv2.resize(frame_rgb, self.out_size, interpolation=cv2.INTER_AREA)
        ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        s = PREFIX + fid * SPF - self.fed_offset
        pcm = self.fed[s: s + SPF] if s >= 0 else np.zeros(SPF, dtype=np.float32)
        pcm16 = (np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes()
        self.stats["frames"] = self.out_idx
        st = self.stats
        if st["first_speech_frame_out"] is None and st["speech_frame_start"] is not None and fid >= st["speech_frame_start"]:
            st["first_speech_frame_out"] = time.time()
        self.on_frame(fid, jpg.tobytes(), pcm16)

    def latency_report(self):
        st = self.stats
        el = time.time() - st["started"] if st["started"] else 0
        r = {"frames_out": st["frames"], "seconds": round(el, 2), "fps_avg": round(st["frames"] / el, 2) if el else 0,
             "late_ticks": st["late_ticks"], "queued_speech_s": round(self.queued_seconds(), 2)}
        if st["first_speech_in"] and st["first_speech_frame_out"]:
            r["speech_to_lips_s"] = round(st["first_speech_frame_out"] - st["first_speech_in"], 3)
        return r

    def close(self):
        self.running = False
        if getattr(self, "thread", None):
            self.thread.join(timeout=5)
        _drift["on"] = False
        try:
            self.sdk.close()
        finally:
            self.sdk.on_frame = None
