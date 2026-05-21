"""
watchdog.py — Auto-recovery DeepSonnet26 / Ollama stuck detector.

Logica:
  Se worker_deep='running' MA la GPU è inattiva (fps alto + gpu_util basso)
  per più di STUCK_SECS secondi → Ollama è bloccato.
  Azione: forza il task a error, restart Ollama, il worker riprende.

FPS alto = GPU libera = Ollama non sta inferendo (il kernel gears gira indisturbato).
Grace period iniziale per dare tempo a Ollama di caricare il modello (~25s).
"""

import threading
import time
import subprocess
import requests

import db          as _db
import fps_monitor as _fps

POLL_SECS   = 5    # intervallo tra check
STUCK_SECS  = 20   # secondi GPU idle consecutivi → intervento
GRACE_SECS  = 25   # ignora i primi N secondi dopo avvio task (caricamento modello)
FPS_HIGH    = 2000 # fps sopra questa soglia = GPU non sta inferendo (CUDA mode)
UTIL_LOW    = 20   # gpu_util % sotto questa soglia = GPU inattiva

_stuck_since  = None
_task_started = None

def _log(action, detail=""):
    try:
        requests.post("http://localhost:5052/api/log/add",
                      json={"type": "watchdog", "action": action, "detail": detail},
                      timeout=2)
    except Exception:
        pass

def _restart_ollama():
    _log("ollama_restart", "avvio restart...")
    # Prova prima systemctl
    try:
        r = subprocess.run(["systemctl", "restart", "ollama"],
                           timeout=15, capture_output=True)
        if r.returncode == 0:
            time.sleep(5)
            if requests.get("http://localhost:11434/api/tags", timeout=5).ok:
                _log("ollama_restart", "✓ systemctl restart ok")
                return True
    except Exception:
        pass
    # Fallback: pkill + ollama serve
    try:
        subprocess.run(["pkill", "-f", "ollama serve"], timeout=5, check=False)
        time.sleep(3)
        subprocess.Popen(["ollama", "serve"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(6)
        if requests.get("http://localhost:11434/api/tags", timeout=5).ok:
            _log("ollama_restart", "✓ restart manuale ok")
            return True
    except Exception as e:
        _log("ollama_restart_fail", str(e)[:80])
    return False

def _watchdog_loop():
    global _stuck_since, _task_started

    while True:
        time.sleep(POLL_SECS)
        try:
            if not _db.task_deep_running():
                _stuck_since  = None
                _task_started = None
                continue

            # Primo rilevamento del task running → segna il tempo
            if _task_started is None:
                _task_started = time.time()

            # Grace period: lascia caricare il modello
            if time.time() - _task_started < GRACE_SECS:
                continue

            state    = _fps.get()
            fps      = state.get("fps", 0)
            gpu_util = state.get("gpu_util", 0)

            # GPU libera = fps sopra soglia E utilizzo basso
            gpu_idle = (fps > FPS_HIGH or fps == 0) and gpu_util < UTIL_LOW

            if gpu_idle:
                if _stuck_since is None:
                    _stuck_since = time.time()
                elif time.time() - _stuck_since >= STUCK_SECS:
                    elapsed = round(time.time() - _stuck_since)
                    task  = _db.task_get_deep_running()
                    tid   = task["id"]    if task else "?"
                    label = task["label"] if task else "?"

                    _log("stuck_detected",
                         f"[{tid}] {label} — GPU idle {elapsed}s "
                         f"fps={fps} util={gpu_util}%")

                    if task:
                        _db.task_force_error(tid)
                        _log("task_reset", f"[{tid}] {label} → error, verrà saltato")

                    _restart_ollama()
                    _stuck_since  = None
                    _task_started = None
            else:
                # GPU attiva → reset contatore stallo
                _stuck_since = None

        except Exception:
            pass


def start():
    t = threading.Thread(target=_watchdog_loop, daemon=True, name="watchdog")
    t.start()
