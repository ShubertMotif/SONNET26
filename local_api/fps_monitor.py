"""
fps_monitor.py — 3 ingranaggi rotanti calcolati in un solo kernel CUDA fuso.

Geometria:
  Gear A (arancione): 8 denti, R=20px, ω=+1.5 rad/s  → centro (25,35)
  Gear B (ciano):     6 denti, R=14px, ω=-2.0 rad/s  → centro (62,25)
  Gear C (verde):     5 denti, R=11px, ω=+3.0 rad/s  → centro (82,55)
  Totale spigoli: 8+6+5=19 · Pezzi: 3 · Target: ~6000 FPS

Un unico ElementwiseKernel → 1 lancio GPU per frame → minima latenza.
"""

import threading
import time
import io
import os

import cupy as cp
import numpy as np
from PIL import Image
import pynvml

# ── Dimensione superficie ──────────────────────────────────────────────────────
W, H = 80, 55          # 4400 pixel → JPEG veloce, target 6000 FPS

# ── Griglia coordinate flat (preparata una sola volta su GPU) ─────────────────
_xi = cp.tile(cp.arange(W, dtype=cp.float32), H)      # (H*W,)
_yi = cp.repeat(cp.arange(H, dtype=cp.float32), W)    # (H*W,)

# ── Kernel CUDA fuso: 3 ingranaggi in un singolo lancio ───────────────────────
_gear_kernel = cp.ElementwiseKernel(
    'float32 x, float32 y, float32 t',
    'uint8 r, uint8 g, uint8 b',
    '''
    /* ── Gear A (arancione) : centro(25,35) R=20 8 denti ω=+1.5 ── */
    float ax=x-25.f, ay=y-35.f;
    float ra=sqrtf(ax*ax+ay*ay);
    float rma=20.f + 5.5f*sinf(8.f*atan2f(ay,ax) + t*1.5f);
    bool ga = ra>7.f && ra<rma;

    /* ── Gear B (ciano)    : centro(62,25) R=14 6 denti ω=-2.0 ── */
    float bx=x-62.f, by=y-25.f;
    float rb=sqrtf(bx*bx+by*by);
    float rmb=14.f + 3.8f*sinf(6.f*atan2f(by,bx) - t*2.0f);
    bool gb = rb>5.f && rb<rmb;

    /* ── Gear C (verde)    : centro(82,55) R=11 5 denti ω=+3.0 ── */
    float cx=x-82.f, cy_=y-55.f;
    float rc=sqrtf(cx*cx+cy_*cy_);
    float rmc=11.f + 3.0f*sinf(5.f*atan2f(cy_,cx) + t*3.0f);
    bool gc = rc>4.f && rc<rmc;

    /* ── Assegna colori ── */
    if(ga){       r=255; g=140; b=40;  }
    else if(gb){  r=30;  g=200; b=255; }
    else if(gc){  r=60;  g=230; b=90;  }
    else{         r=8;   g=8;   b=18;  }
    ''',
    '_gear_kernel'
)

# ── Output buffers su GPU (riutilizzati ogni frame) ───────────────────────────
_r_buf = cp.empty(H * W, dtype=cp.uint8)
_g_buf = cp.empty(H * W, dtype=cp.uint8)
_b_buf = cp.empty(H * W, dtype=cp.uint8)

# ── Stato condiviso ───────────────────────────────────────────────────────────
_lock    = threading.Lock()
_state   = {"fps": 0.0, "cuda_ms": 0.0, "gpu_util": 0, "temp_c": 0, "mem_gb": 0.0}
_frame   = b""
_running = False
_thread  = None

# ── NVML ──────────────────────────────────────────────────────────────────────
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
        u = pynvml.nvmlDeviceGetUtilizationRates(_handle).gpu
        t = pynvml.nvmlDeviceGetTemperature(_handle, pynvml.NVML_TEMPERATURE_GPU)
        m = pynvml.nvmlDeviceGetMemoryInfo(_handle)
        return u, t, round(m.used / 1024**3, 1)
    except Exception:
        return 0, 0, 0.0

# ── Frame render ───────────────────────────────────────────────────────────────
def _render_frame(t_anim: float) -> bytes:
    t32 = cp.float32(t_anim)
    _gear_kernel(_xi, _yi, t32, _r_buf, _g_buf, _b_buf)
    cp.cuda.Stream.null.synchronize()

    rgb = cp.stack([
        _r_buf.reshape(H, W),
        _g_buf.reshape(H, W),
        _b_buf.reshape(H, W),
    ], axis=2)
    arr = cp.asnumpy(rgb)

    img = Image.fromarray(arr, "RGB").resize((W * 2, H * 2), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()

# ── Loop principale ────────────────────────────────────────────────────────────
def _loop():
    global _running, _frame

    frame_count = 0
    t0          = time.perf_counter()
    t_anim      = 0.0

    while _running:
        cuda_t0 = time.perf_counter()
        jpeg    = _render_frame(t_anim)
        cuda_ms = round((time.perf_counter() - cuda_t0) * 1000, 2)

        with _lock:
            _frame = jpeg
            _state["cuda_ms"] = cuda_ms

        t_anim      += 0.04
        frame_count += 1

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
