"""
fps_monitor.py — Loop pygame + CUDA (cupy) headless per misurare FPS GPU reali.

Avvio: fps_monitor.start()
Lettura: fps_monitor.get()  →  {"fps": 87.3, "cuda_ms": 1.2, "gpu_util": 45, "temp_c": 52, "mem_gb": 9.5}
Stop:   fps_monitor.stop()
"""

import threading
import time
import os

import pygame
import cupy as cp
import pynvml

# ── Stato condiviso ───────────────────────────────────────────────────────────

_lock    = threading.Lock()
_state   = {"fps": 0.0, "cuda_ms": 0.0, "gpu_util": 0, "temp_c": 0, "mem_gb": 0.0}
_running = False
_thread  = None

# ── Dimensione workload CUDA (256×256 float32) ────────────────────────────────
_WSIZE = 256

# ── Inizializza NVML una volta ────────────────────────────────────────────────
try:
    pynvml.nvmlInit()
    _nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    _NVML_OK = True
except Exception:
    _NVML_OK = False


def _nvml_stats():
    if not _NVML_OK:
        return 0, 0, 0.0
    try:
        util  = pynvml.nvmlDeviceGetUtilizationRates(_nvml_handle).gpu
        temp  = pynvml.nvmlDeviceGetTemperature(_nvml_handle, pynvml.NVML_TEMPERATURE_GPU)
        mem   = pynvml.nvmlDeviceGetMemoryInfo(_nvml_handle)
        mem_gb = round(mem.used / 1024**3, 1)
        return util, temp, mem_gb
    except Exception:
        return 0, 0, 0.0


def _loop():
    global _running

    # pygame headless (nessuna finestra)
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.init()
    surf   = pygame.Surface((_WSIZE, _WSIZE))
    clock  = pygame.time.Clock()

    # Array CUDA fisso per il workload
    a = cp.random.rand(_WSIZE, _WSIZE, dtype=cp.float32)
    b = cp.random.rand(_WSIZE, _WSIZE, dtype=cp.float32)

    frame_count = 0
    t0 = time.perf_counter()

    while _running:
        # ── Frame pygame (headless) ──
        frame_count += 1
        color = (frame_count % 255, 100, 200)
        pygame.draw.rect(surf, color, (0, 0, _WSIZE, _WSIZE))
        pygame.draw.circle(surf, (255 - color[0], color[1], color[2]),
                           (_WSIZE // 2, _WSIZE // 2), _WSIZE // 4)

        # ── Workload CUDA: matmul + norm ──
        cuda_t0 = time.perf_counter()
        c = cp.dot(a, b)
        cp.linalg.norm(c)
        cp.cuda.Stream.null.synchronize()
        cuda_ms = round((time.perf_counter() - cuda_t0) * 1000, 2)

        # ── Aggiorna stato ogni secondo ──
        elapsed = time.perf_counter() - t0
        if elapsed >= 1.0:
            fps = round(frame_count / elapsed, 1)
            util, temp, mem_gb = _nvml_stats()
            with _lock:
                _state["fps"]      = fps
                _state["cuda_ms"]  = cuda_ms
                _state["gpu_util"] = util
                _state["temp_c"]   = temp
                _state["mem_gb"]   = mem_gb
            frame_count = 0
            t0 = time.perf_counter()

        clock.tick(0)  # max speed, nessun cap

    pygame.quit()


# ── API pubblica ───────────────────────────────────────────────────────────────

def start():
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_loop, daemon=True, name="fps-monitor")
    _thread.start()


def stop():
    global _running
    _running = False


def get():
    with _lock:
        return dict(_state)


def is_running():
    return _running and (_thread is not None) and _thread.is_alive()
