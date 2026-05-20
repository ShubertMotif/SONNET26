"""
fps_monitor.py — Plasma GPU pixel-per-pixel via cupy + pygame headless.

Ogni frame: cupy calcola un'equazione plasma (sin/cos su griglia W×H) → JPEG.
Quando la GPU è occupata (DeepSonnet26 in inferenza), il plasma rallenta.
Flask serve /api/fpsbox/frame come MJPEG-like stream.
"""

import threading
import time
import io
import os

import pygame
import cupy as cp
import numpy as np
from PIL import Image
import pynvml

# ── Config ────────────────────────────────────────────────────────────────────
W, H = 160, 100          # dimensione animazione (pixel)
TARGET_FPS = 0           # 0 = max speed

# ── Stato condiviso ───────────────────────────────────────────────────────────
_lock    = threading.Lock()
_state   = {"fps": 0.0, "cuda_ms": 0.0, "gpu_util": 0, "temp_c": 0, "mem_gb": 0.0}
_frame   = b""           # ultimo frame JPEG
_running = False
_thread  = None

# ── NVML ─────────────────────────────────────────────────────────────────────
try:
    pynvml.nvmlInit()
    _handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    _NVML = True
except Exception:
    _NVML = False

def _nvml_stats():
    if not _NVML:
        return 0, 0, 0.0
    try:
        u  = pynvml.nvmlDeviceGetUtilizationRates(_handle).gpu
        t  = pynvml.nvmlDeviceGetTemperature(_handle, pynvml.NVML_TEMPERATURE_GPU)
        m  = pynvml.nvmlDeviceGetMemoryInfo(_handle)
        return u, t, round(m.used / 1024**3, 1)
    except Exception:
        return 0, 0, 0.0

# ── Griglia CUDA (preparata una volta) ───────────────────────────────────────
_x = cp.linspace(0, 2 * cp.pi, W, dtype=cp.float32)
_y = cp.linspace(0, 2 * cp.pi, H, dtype=cp.float32)
_X, _Y = cp.meshgrid(_x, _y)   # (H, W)

def _plasma_frame(t: float) -> bytes:
    """
    Calcola il plasma interamente su GPU (cupy), trasferisce su CPU,
    converte in JPEG. Restituisce bytes.
    """
    t32 = cp.float32(t)

    # Formula plasma: somma di onde sinusoidali
    v = (cp.sin(_X + t32)
       + cp.sin(_Y * 0.7 + t32 * 1.3)
       + cp.sin((_X + _Y) * 0.5 + t32 * 0.8)
       + cp.sin(cp.sqrt(_X**2 + _Y**2 + 0.1) - t32 * 1.5))

    v = (v + 4.0) / 8.0   # normalizza [0,1]

    # Colormap neon: R/G/B su frequenze diverse
    r = cp.clip(cp.sin(v * cp.pi * 3.0) * 255, 0, 255)
    g = cp.clip(cp.sin(v * cp.pi * 3.0 + 2.09) * 200 + 55, 0, 255)
    b = cp.clip(cp.sin(v * cp.pi * 3.0 + 4.19) * 255, 0, 255)

    rgb = cp.stack([r, g, b], axis=2).astype(cp.uint8)
    cp.cuda.Stream.null.synchronize()

    arr = cp.asnumpy(rgb)          # (H, W, 3)  CPU
    img = Image.fromarray(arr, "RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def _loop():
    global _running, _frame

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.init()

    clock       = pygame.time.Clock()
    frame_count = 0
    t0          = time.perf_counter()
    t_anim      = 0.0

    while _running:
        cuda_t0 = time.perf_counter()
        jpeg    = _plasma_frame(t_anim)
        cuda_ms = round((time.perf_counter() - cuda_t0) * 1000, 2)

        with _lock:
            _frame = jpeg
            _state["cuda_ms"] = cuda_ms

        t_anim      += 0.05
        frame_count += 1

        # Aggiorna stats ogni secondo
        elapsed = time.perf_counter() - t0
        if elapsed >= 1.0:
            fps = round(frame_count / elapsed, 1)
            u, t, m = _nvml_stats()
            with _lock:
                _state["fps"]      = fps
                _state["gpu_util"] = u
                _state["temp_c"]   = t
                _state["mem_gb"]   = m
            frame_count = 0
            t0 = time.perf_counter()

        if TARGET_FPS > 0:
            clock.tick(TARGET_FPS)

    pygame.quit()


# ── API pubblica ───────────────────────────────────────────────────────────────

def start():
    global _running, _thread
    if _running:
        return
    _running = True
    _thread  = threading.Thread(target=_loop, daemon=True, name="fps-monitor")
    _thread.start()

def stop():
    global _running
    _running = False

def get():
    with _lock:
        return dict(_state)

def get_frame():
    with _lock:
        return _frame if _frame else None

def is_running():
    return _running and _thread is not None and _thread.is_alive()
