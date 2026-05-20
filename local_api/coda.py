import threading
import subprocess
import uuid
import json
import os
import time
import datetime
import requests

CODA_FILE  = os.path.join(os.path.dirname(__file__), "../data/coda.json")
_TEMPLATE_FILE = os.path.join(os.path.dirname(__file__), "../templates/report_base.html")

def _load_template():
    try:
        with open(_TEMPLATE_FILE, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

_DEEP_SYSTEM = """Sei DeepSonnet26, AI locale su RTX 3060. Generi report HTML completi in italiano.

REGOLA FONDAMENTALE: rispondi SOLO con HTML valido, niente testo fuori dal tag <html>.

USA QUESTO TEMPLATE BASE (CSS già incluso, non riscrivere gli stili):

""" + _load_template() + """

ISTRUZIONI:
- Sostituisci {{TITOLO}}, {{CONTENUTO}}, {{MODELLO}} = DeepSonnet26, {{DATA}} con la data odierna
- Usa i blocchi CSS già definiti: .card, .grid, .tbl, .props, .stat, .box-*, .tag-*, .list, .formula, .timeline, .bar-wrap
- Scegli i blocchi adatti al contenuto — non usarli tutti, solo quelli che servono
- Dati accurati, layout a sezioni logiche con h2/h3
- Output: HTML completo e autosufficiente"""
DIR_CLAUDE = "/home/mattia/Scrivania/SONNET26/output_claude"
DIR_DEEP   = "/mnt/sda3/SONNET26_DATA/output_deep"

_lock = threading.Lock()
_worker_thread = None


def _load():
    os.makedirs(os.path.dirname(CODA_FILE), exist_ok=True)
    if not os.path.exists(CODA_FILE):
        return []
    try:
        with open(CODA_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save(tasks):
    os.makedirs(os.path.dirname(CODA_FILE), exist_ok=True)
    with open(CODA_FILE, "w") as f:
        json.dump(tasks, f, indent=2, ensure_ascii=False)


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add(task_type, payload, label=""):
    """Aggiunge un task standard alla coda."""
    task = {
        "id": str(uuid.uuid4())[:8],
        "type": task_type,
        "payload": payload,
        "label": label or payload[:60],
        "status": "pending",
        "created": _now(),
        "started": None,
        "finished": None,
        "output": "",
    }
    with _lock:
        tasks = _load()
        tasks.append(task)
        _save(tasks)
    _ensure_worker()
    return task


def add_dual(label, brief, output_claude, file_claude, write_claude=True):
    """
    Task dual: Claude ha già completato la sua parte,
    DeepSonnet26 viene accodato per lavorare sullo stesso brief.
    write_claude=False se il file è già stato salvato dal chiamante.
    """
    path_claude = os.path.join(DIR_CLAUDE, file_claude)
    if write_claude:
        os.makedirs(DIR_CLAUDE, exist_ok=True)
        with open(path_claude, "w", encoding="utf-8") as f:
            f.write(output_claude)

    base = os.path.splitext(file_claude)[0]
    ext  = os.path.splitext(file_claude)[1] or ".txt"
    # strip _claude suffix se presente, poi aggiungi _deepsonnet26
    base_clean = base[:-7] if base.endswith("_claude") else base
    file_deep = base_clean + "_deepsonnet26" + ext
    path_deep = os.path.join(DIR_DEEP, file_deep)

    task = {
        "id": str(uuid.uuid4())[:8],
        "type": "dual",
        "payload": brief,
        "label": label or brief[:60],
        "status": "running",
        "created": _now(),
        "started": _now(),
        "finished": None,
        "output": "",
        # Claude (già fatto)
        "worker_claude": "done",
        "output_claude": output_claude[:2000],
        "file_claude": file_claude,
        "path_claude": path_claude,
        # DeepSonnet26 (in attesa)
        "worker_deep": "pending",
        "output_deep": "",
        "file_deep": file_deep,
        "path_deep": path_deep,
        "started_deep": None,
        "finished_deep": None,
    }
    with _lock:
        tasks = _load()
        tasks.append(task)
        _save(tasks)
    _ensure_worker()
    return task


def list_tasks(status_filter=None):
    with _lock:
        tasks = _load()
    if status_filter:
        tasks = [t for t in tasks if t["status"] == status_filter]
    return tasks


def get(task_id):
    with _lock:
        tasks = _load()
    return next((t for t in tasks if t["id"] == task_id), None)


def cancel(task_id):
    with _lock:
        tasks = _load()
        for t in tasks:
            if t["id"] == task_id and t["status"] in ("pending", "running"):
                t["status"] = "cancelled"
                t["finished"] = _now()
                if t["type"] == "dual":
                    t["worker_deep"] = "cancelled"
                    t["finished_deep"] = _now()
        _save(tasks)


def clear_done():
    with _lock:
        tasks = _load()
        tasks = [t for t in tasks if t["status"] in ("pending", "running")]
        _save(tasks)


def _update_standard(task_id, status, output=None, finished=False):
    with _lock:
        tasks = _load()
        for t in tasks:
            if t["id"] == task_id:
                t["status"] = status
                if output is not None:
                    t["output"] = output
                if finished:
                    t["finished"] = _now()
                elif status == "running":
                    t["started"] = _now()
        _save(tasks)


def _run_standard(task):
    _update_standard(task["id"], "running")
    try:
        if task["type"] == "shell":
            result = subprocess.run(
                task["payload"], shell=True, capture_output=True,
                text=True, timeout=60, cwd="/home/mattia"
            )
            out = (result.stdout + result.stderr).strip() or "(nessun output)"
            _update_standard(task["id"], "done" if result.returncode == 0 else "error", out, finished=True)

        elif task["type"] == "python":
            result = subprocess.run(
                ["python3", "-c", task["payload"]],
                capture_output=True, text=True, timeout=60, cwd="/home/mattia"
            )
            out = (result.stdout + result.stderr).strip() or "(nessun output)"
            _update_standard(task["id"], "done" if result.returncode == 0 else "error", out, finished=True)

        elif task["type"] == "prompt":
            try:
                r = requests.post(
                    "http://localhost:5051/api/chat",
                    json={"prompt": task["payload"]},
                    timeout=120
                )
                resp = r.json()
                out = resp.get("response", resp.get("reply", resp.get("message", str(resp))))
                _update_standard(task["id"], "done", out, finished=True)
            except Exception as e:
                _update_standard(task["id"], "error", str(e), finished=True)

        elif task["type"] == "note":
            _update_standard(task["id"], "done", task["payload"], finished=True)

        else:
            _update_standard(task["id"], "error", f"tipo sconosciuto: {task['type']}", finished=True)

    except subprocess.TimeoutExpired:
        _update_standard(task["id"], "error", "[timeout 60s]", finished=True)
    except Exception as e:
        _update_standard(task["id"], "error", str(e), finished=True)


def _update_dual_deep(task_id, worker_status, output_deep=None, done=False):
    completed_label = None
    completed_file  = None
    completed_ok    = False
    with _lock:
        tasks = _load()
        for t in tasks:
            if t["id"] == task_id:
                t["worker_deep"] = worker_status
                if output_deep is not None:
                    t["output_deep"] = output_deep
                if worker_status == "running" and not t.get("started_deep"):
                    t["started_deep"] = _now()
                if done:
                    t["finished_deep"] = _now()
                    t["finished"] = _now()
                    t["status"] = "done" if worker_status == "done" else "error"
                    completed_label = t.get("label", "")
                    completed_file  = t.get("file_deep", "")
                    completed_ok    = (worker_status == "done")
        _save(tasks)
    if done and completed_label is not None:
        icon = "✓" if completed_ok else "✗"
        try:
            requests.post("http://localhost:5052/api/log/add", json={
                "type": "deep", "action": completed_label,
                "detail": f"{icon} {completed_file}"
            }, timeout=2)
        except Exception:
            pass


def _run_dual_deep(task):
    _update_dual_deep(task["id"], "running")
    try:
        # Stream direttamente da Ollama per aggiornamenti real-time
        r = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": "deepsonnet26",
                "messages": [
                    {"role": "system", "content": _DEEP_SYSTEM},
                    {"role": "user", "content": task["payload"]},
                ],
                "stream": True,
            },
            stream=True,
            timeout=300,
        )

        accumulated = ""
        last_flush = 0
        os.makedirs(DIR_DEEP, exist_ok=True)

        for line in r.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
                token = chunk.get("message", {}).get("content", "")
                accumulated += token
                # Aggiorna CODA ogni ~50 chars per non martellare il lock
                if len(accumulated) - last_flush >= 300:
                    _update_dual_deep(task["id"], "running", accumulated[:2000])
                    last_flush = len(accumulated)
                if chunk.get("done"):
                    break
            except Exception:
                continue

        # Salva file completo
        with open(task["path_deep"], "w", encoding="utf-8") as f:
            f.write(accumulated)

        _update_dual_deep(task["id"], "done", accumulated[:2000], done=True)
    except Exception as e:
        _update_dual_deep(task["id"], "error", str(e), done=True)


def _worker_loop():
    while True:
        task_to_run = None
        with _lock:
            tasks = _load()
            for t in tasks:
                if t["status"] == "pending" and t["type"] != "dual":
                    task_to_run = t
                    break
            if task_to_run is None:
                for t in tasks:
                    if t["type"] == "dual" and t.get("worker_deep") == "pending":
                        task_to_run = t
                        break

        if task_to_run:
            if task_to_run["type"] == "dual":
                _run_dual_deep(task_to_run)
            else:
                _run_standard(task_to_run)
        else:
            time.sleep(2)


def _ensure_worker():
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
        _worker_thread.start()


_ensure_worker()
