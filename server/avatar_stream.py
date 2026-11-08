"""Real-time Ditto avatar streaming: audio in, video frames out, at 25 fps.

Built on Ditto's online pipeline (stream_pipeline_online.StreamSDK, TensorRT, HuBERT chunk features).
A session keeps the avatar alive continuously: when no speech is queued it is fed silence, so the
face idles (blinks, subtle motion) and speech starts on the next 0.2 s tick.

Approved look, identical to the v6 FINAL Colab notebook (Cell 6), applied to every portrait:
mouth 0.9, smoothing 3, Neutral emotion, own-face reference, head 0.6 / 0.6 / 0.5, seed 1024,
lips close in pauses (lips-only vad_alpha, the notebook's pause_alpha formula), head-drift stabiliser.
Real-time speed (motion steps, overlap, output size) is set separately for latency; see below.
"""
import math
import os
import queue
import random
import threading
import time
from collections import deque

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

# ============ APPROVED LOOK: expression + lip-sync settings, identical to ditto_colab v6 FINAL (Cell 6) ============
MOUTH_GAIN = 0.90               # jaw opening calibrated to the client's real speaking video
SMOOTHING = 3                   # smooth transitions between mouth shapes
HEAD_NOD, HEAD_TURN, HEAD_TILT = 0.6, 0.6, 0.5   # subtle, natural head movement
PAUSE_CLOSE = 1.0               # lips close in speech pauses (1 = fully, 0 = off)
HEAD_STABILISE = True           # removes slow head/chin drift (keeps quick natural nods)
DRIFT_WINDOW_S = 1.5            # head movement slower than this counts as drift
MAX_SIZE, CROP_SCALE, SEED = 1280, 2.3, 1024

# ============ REAL-TIME SPEED: chosen for latency (tested: 1.35 s speech-to-lips on the RTX 4090) ============
# The notebook rendered offline with 50 steps / overlap 10 (motion in 2.8 s blocks, ~3 s+ latency live).
# Ditto's real-time cfg uses 10 steps / overlap 70 (0.4 s blocks). Both stay overridable per stream
# (?steps=..&overlap=..) for quality comparisons; keep late_ticks near 0 when raising steps.
SAMPLING_STEPS = 10
OVERLAP_V2 = 70
OUT_HEIGHT, JPEG_QUALITY = 960, 80   # stream frame size / quality as tested (0 = full resolution)
# ================================================================================================================

FINAL_STYLE = {
    "smo_k_d": SMOOTHING,
    "emo": 4,                   # 4 = Neutral (no stored "smile" emotion clip)
    "ch_info": None,            # use each photo's own face as the reference
    "use_d_keys": {"exp": MOUTH_GAIN, "pitch": HEAD_NOD, "yaw": HEAD_TURN, "roll": HEAD_TILT, "t": HEAD_TURN},
    "sampling_timesteps": SAMPLING_STEPS,
    "overlap_v2": OVERLAP_V2,
    "crop_scale": CROP_SCALE,
    "max_size": MAX_SIZE,
}


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


def _frame_db(samples):
    f = samples[: len(samples) // SPF * SPF].reshape(-1, SPF)
    return 20 * np.log10(np.sqrt((f ** 2).mean(1)) + 1e-6)


class PauseGate:
    """The notebook's pause_alpha, per frame, for a live stream.

    Same formula: dB per 40 ms frame, normalised between the 10th and 95th percentile of the speech,
    speech where the level is above 0.3, lips open 2 frames (~80 ms) early, 5-frame (~200 ms) soft
    open/close, alpha = 1 - strength * (1 - gate). The notebook took the percentiles over the whole
    clip; here they come from all speech received so far (last 30 s), which includes speech that is
    queued but not played yet, so a TTS answer that arrives faster than real time is gated like a clip.
    Idle silence the server inserts between answers always counts as a pause.
    """

    def __init__(self, strength=PAUSE_CLOSE, lead=2, window_frames=30 * FPS):
        self.strength, self.lead = strength, lead
        self.stats = deque(maxlen=window_frames)   # dB of received speech frames (incl. queued)
        self._carry = np.zeros(0, dtype=np.float32)
        self.db = []        # per fed frame
        self.real = []      # per fed frame: True = client speech, False = inserted silence

    def add_received(self, samples):
        a = np.concatenate([self._carry, samples])
        n = len(a) // SPF * SPF
        if n:
            self.stats.extend(_frame_db(a[:n]).tolist())
        self._carry = a[n:]

    def push(self, chunk, n_real):
        self.db.extend(_frame_db(chunk).tolist())
        self.real.extend(i * SPF < n_real for i in range(len(chunk) // SPF))

    def alpha(self, fid):
        """vad_alpha for frame fid; uses frames up to fid + lead + 2 if they have been fed."""
        n = len(self.db)
        if n == 0 or not self.stats:
            return float(np.clip(1 - self.strength, 0, 1))
        lo, hi = np.percentile(self.stats, 10), np.percentile(self.stats, 95)
        span = max(hi - lo, 1e-6)

        def speech(j):
            return 1.0 if self.real[j] and np.clip((self.db[j] - lo) / span, 0, 1) > 0.3 else 0.0

        def lead_max(i):                     # open ~80 ms before the sound
            return max(speech(j) for j in range(i, min(i + self.lead + 1, n)))

        gate = np.mean([lead_max(min(max(i, 0), n - 1)) for i in range(fid - 2, fid + 3)])   # edge-padded 5-frame mean
        return float(np.clip(1 - self.strength * (1 - gate), 0, 1))


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

    on_frame(frame_idx, answer, jpeg_bytes, pcm16_bytes): called from a worker thread for every 40 ms frame, with the
    640 audio samples that belong to it, so the client can play picture and sound in sync. answer is the number of
    the answer whose speech the frame's first sample carries (answers count 1, 2, ... in the order they are sent,
    closed by end_answer() or interrupt()); 0 = idle, or an interrupted answer, whose audio is sent silent.
    """

    def __init__(self, sdk: AvatarSDK, source_path, on_frame, out_height=OUT_HEIGHT, jpeg_quality=JPEG_QUALITY,
                 sampling_timesteps=None, overlap_v2=None, style=None):
        self.sdk, self.on_frame = sdk, on_frame
        self.out_height, self.jpeg_quality = out_height, jpeg_quality
        self.speech = bytearray()            # queued speech, float32 bytes at 16 kHz
        self.speech_answers = deque()        # [answer, samples] runs labelling self.speech, in order
        self.answer = 1                      # the answer that incoming speech belongs to
        self.silenced_upto = 0               # answers up to this number were interrupted
        self.lock = threading.Lock()
        self.fed = np.zeros(PREFIX, dtype=np.float32)   # all audio fed so far, incl. the Ditto prefix
        self.fed_answers = np.zeros(PREFIX, dtype=np.int32)   # answer number per fed sample (0 = idle)
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
        if overlap_v2:
            kwargs["overlap_v2"] = overlap_v2
        self.settings = {k: kwargs[k] for k in ("sampling_timesteps", "overlap_v2")}
        random.seed(SEED); np.random.seed(SEED)
        try:
            import torch
            torch.manual_seed(SEED); torch.cuda.manual_seed_all(SEED)
        except ImportError:
            pass
        sdk.on_frame = self._emit
        _drift["on"], _drift["state"] = HEAD_STABILISE, {}
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
            if self.speech_answers and self.speech_answers[-1][0] == self.answer:
                self.speech_answers[-1][1] += len(a)
            elif len(a):
                self.speech_answers.append([self.answer, len(a)])
            self.gate.add_received(a)

    def end_answer(self):
        """The current answer's speech is complete; speech after this belongs to the next answer."""
        with self.lock:
            self.answer += 1

    def interrupt(self):
        """Drop queued speech (barge-in) and close the current answer. Frames already in the pipeline still play out
        (~latency), with their audio silenced."""
        with self.lock:
            self.speech = bytearray()
            self.speech_answers.clear()
            self.silenced_upto = self.answer
            self.answer += 1

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
                    labels = self._take_answer_labels(n)
                if n and self.stats["speech_frame_start"] is None:
                    self.stats["speech_frame_start"] = (len(self.fed) - PREFIX + self.fed_offset) // SPF
                chunk = np.concatenate([take, np.zeros(TICK_SAMPLES - n, dtype=np.float32)])
                self.gate.push(chunk, n)
                self.fed = np.concatenate([self.fed, chunk])
                self.fed_answers = np.concatenate([self.fed_answers, labels, np.zeros(TICK_SAMPLES - n, dtype=np.int32)])
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

    def _take_answer_labels(self, n):
        """Answer number for each of the next n queued samples (call with self.lock held)."""
        labels = np.empty(n, dtype=np.int32)
        pos = 0
        while pos < n:
            run = self.speech_answers[0]
            k = min(run[1], n - pos)
            labels[pos: pos + k] = run[0]
            pos += k
            run[1] -= k
            if run[1] == 0:
                self.speech_answers.popleft()
        return labels

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
            self.fed, self.fed_answers, self.fed_offset = self.fed[cut:], self.fed_answers[cut:], self.fed_offset + cut
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
        answer = int(self.fed_answers[s]) if 0 <= s < len(self.fed_answers) else 0
        if answer and answer <= self.silenced_upto:      # interrupted: the face finishes the motion, without sound
            answer, pcm = 0, np.zeros(SPF, dtype=np.float32)
        pcm16 = (np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes()
        self.stats["frames"] = self.out_idx
        st = self.stats
        if st["first_speech_frame_out"] is None and st["speech_frame_start"] is not None and fid >= st["speech_frame_start"]:
            st["first_speech_frame_out"] = time.time()
        self.on_frame(fid, answer, jpg.tobytes(), pcm16)

    def latency_report(self):
        st = self.stats
        el = time.time() - st["started"] if st["started"] else 0
        r = {"frames_out": st["frames"], "seconds": round(el, 2), "fps_avg": round(st["frames"] / el, 2) if el else 0,
             "late_ticks": st["late_ticks"], "queued_speech_s": round(self.queued_seconds(), 2), **self.settings}
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
