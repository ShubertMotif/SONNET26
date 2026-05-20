from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor
import subprocess, os, json, requests, time, datetime

import coda as coda_module
import scheduler as sched_module
import report as report_module
import fps_monitor as fps_module

app = Flask(__name__)
CORS(app)

SONNET26_DIR  = "/home/mattia/Scrivania/SONNET26"
DIR_CLAUDE    = os.path.join(SONNET26_DIR, "output_claude")
DIR_DEEP      = "/mnt/sda3/SONNET26_DATA/output_deep"
LOG_PATH      = os.path.join(SONNET26_DIR, "log.jsonl")
STATE_FILE    = os.path.join(SONNET26_DIR, "data", "api_state.json")

ALLOWED_ROOTS = ["/home/mattia", "/mnt/sda3"]

# ── Log cache in memoria (no disk read a ogni poll) ──────────
_log_cache = []

def _log_prime():
    """Carica il log dal disco una sola volta all'avvio."""
    global _log_cache
    entries = []
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try: entries.append(json.loads(line))
                    except: pass
    except FileNotFoundError:
        pass
    _log_cache = entries[-500:]

# ── Stato DS persistente ─────────────────────────────────────
_ds_on = False

def _load_state():
    global _ds_on
    try:
        with open(STATE_FILE) as f:
            _ds_on = json.load(f).get("ds_on", False)
    except Exception:
        pass

def _save_state():
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump({"ds_on": _ds_on}, f)
    except Exception:
        pass

_load_state()
_log_prime()
fps_module.start()

# ── Filetree cache (TTL 30s) ─────────────────────────────────
_ftcache = {"ts": 0, "ssd": [], "deep": []}

def _get_filetree():
    if time.time() - _ftcache["ts"] < 30:
        return {"ssd": _ftcache["ssd"], "deep": _ftcache["deep"]}
    _ftcache["ssd"]  = filetree_build(SONNET26_DIR, 2)
    _ftcache["deep"] = filetree_build("/mnt/sda3/SONNET26_DATA", 2)
    _ftcache["ts"]   = time.time()
    return {"ssd": _ftcache["ssd"], "deep": _ftcache["deep"]}

# ── Helpers ─────────────────────────────────────────────────
def is_allowed(path):
    path = os.path.realpath(path)
    return any(path.startswith(r) for r in ALLOWED_ROOTS)

def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def log_append(type_, action, detail=""):
    entry = {"ts": now_str(), "type": type_, "action": action, "detail": detail}
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _log_cache.append(entry)
        if len(_log_cache) > 500:
            del _log_cache[:-500]
    except Exception:
        pass

def read_log(n=40):
    return _log_cache[-n:]

def hw_data():
    try:
        r = requests.get("http://localhost:5050/api/stats", timeout=3)
        return r.json()
    except Exception:
        pass
    # Fallback: GPU diretto via nvidia-smi
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,"
             "temperature.gpu,power.draw,clocks.current.graphics",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        p = [x.strip() for x in out.split(",")]
        return {"gpu": {
            "gpu_util_pct":  int(p[0]),
            "mem_util_pct":  int(p[1]),
            "mem_used_mib":  int(p[2]),
            "mem_total_mib": int(p[3]),
            "temp_c":        int(p[4]),
            "power_draw_w":  float(p[5]),
            "clock_gpu_mhz": int(p[6]),
        }}
    except Exception as e:
        return {"error": str(e)}

def services_data():
    checks = {
        "sysmon":    "http://localhost:5050/api/stats",
        "deepsonnet":"http://localhost:5051/api/status",
        "api":       "http://localhost:5052/api/status",
        "ollama":    "http://localhost:11434/api/tags",
    }
    def _check(item):
        name, url = item
        try:
            r = requests.get(url, timeout=2)
            return name, "online" if r.ok else "offline"
        except Exception:
            return name, "offline"
    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(ex.map(_check, checks.items()))

def output_files():
    def scan(path):
        try:
            files = []
            for f in sorted(os.listdir(path), key=lambda x: -os.path.getmtime(os.path.join(path,x))):
                fp = os.path.join(path, f)
                if os.path.isfile(fp):
                    files.append({"name": f, "size": os.path.getsize(fp),
                                  "modified": int(os.path.getmtime(fp))})
            return files
        except Exception:
            return []
    return {"claude": scan(DIR_CLAUDE), "deep": scan(DIR_DEEP)}

def filetree_build(path, depth=2):
    skip = {"SONNETvenv", "__pycache__", ".git", "venv", "node_modules"}
    if depth < 0 or not os.path.exists(path):
        return []
    items = []
    try:
        names = sorted(os.listdir(path),
                       key=lambda n: (not os.path.isdir(os.path.join(path,n)), n.lower()))
    except Exception:
        return []
    for name in names:
        if name in skip or name.startswith("."):
            continue
        full = os.path.join(path, name)
        try:
            s = os.stat(full)
            is_dir = os.path.isdir(full)
            node = {"name": name, "path": full, "is_dir": is_dir,
                    "size": s.st_size, "modified": int(s.st_mtime)}
            if is_dir and depth > 0:
                node["children"] = filetree_build(full, depth - 1)
            items.append(node)
        except Exception:
            pass
    return items

# ── Pagine HTML ──────────────────────────────────────────────
@app.route("/")
def index():
    return send_file(os.path.join(SONNET26_DIR, "regno.html"))

@app.route("/output/claude/<path:filename>")
def serve_claude(filename):
    return send_file(os.path.join(DIR_CLAUDE, filename))

@app.route("/output/deep/<path:filename>")
def serve_deep(filename):
    return send_file(os.path.join(DIR_DEEP, filename))

# ── Dashboard aggregato ──────────────────────────────────────
@app.route("/api/dashboard")
def dashboard():
    tasks     = coda_module.list_tasks()
    dual      = [t for t in tasks if t.get("type") == "dual"]
    standard  = [t for t in tasks if t.get("type") != "dual"]
    sched     = sched_module.status()

    return jsonify({
        "ts":        now_str(),
        "hw":        hw_data(),
        "services":  services_data(),
        "log":       read_log(30),
        "coda":      {
            "tasks":   sorted(tasks, key=lambda t: t.get("created",""))[-40:],
            "pending": sum(1 for t in tasks if t["status"] == "pending"),
            "running": sum(1 for t in tasks if t["status"] == "running"),
            "total":   len(tasks),
        },
        "dual":      dual[-10:],
        "scheduler": sched,
        "ds":        {"on": _ds_on},
        "gpu_fps":   next((t["toks_s"] for t in reversed(dual) if t.get("toks_s")), None),
        "output":    output_files(),
        "filetree":  _get_filetree(),
    })

# ── DS toggle ────────────────────────────────────────────────
@app.route("/api/ds", methods=["GET"])
def ds_get():
    return jsonify({"on": _ds_on})

@app.route("/api/ds/toggle", methods=["POST"])
def ds_toggle():
    global _ds_on
    _ds_on = not _ds_on
    _save_state()
    log_append("system", "ds_toggle", f"DeepSonnet segue: {'ON' if _ds_on else 'OFF'}")
    return jsonify({"on": _ds_on})

# ── Task unificato (gestisce DS follow server-side) ──────────
@app.route("/api/task", methods=["POST"])
def task_add():
    global _ds_on
    data    = request.json or {}
    type_   = data.get("type", "shell")
    payload = data.get("payload", "").strip()
    label   = data.get("label", payload[:50])
    if not payload:
        return jsonify({"error": "payload mancante"}), 400

    # "prompt" / "AI" → dual task: Deep genera HTML subito, Claude lato pending
    if type_ == "prompt":
        task = coda_module.add_dual(
            label=label,
            brief=payload,
            output_claude="",
            file_claude="",          # auto-generato da _slugify(label)
            write_claude=False,
            autostart=True,          # DeepSonnet26 parte immediatamente
            worker_claude_init="pending",
        )
        log_append("claude", "dual_input", f"[AI→dual] {payload[:60]}")
        log_append("deep",   label,        f"→ auto: {task['file_deep']}")
        return jsonify({"task": task, "ds_task": None, "ds_on": _ds_on})

    task = coda_module.add(type_, payload, label)
    log_append("coda", "task_add", f"[{type_}] {payload[:60]}")

    ds_task = None
    if _ds_on and type_ != "note":
        ds_prompt = {
            "shell":  f"Dato questo comando shell, spiega cosa fa e l'output atteso:\n{payload}",
            "python": f"Analizza questo codice Python e mostra il risultato:\n{payload}",
        }.get(type_, payload)
        ds_task = coda_module.add("prompt", ds_prompt, f"◈DS {label[:40]}")
        log_append("coda", "ds_follow", f"◈DS accodato per: {payload[:60]}")

    return jsonify({"task": task, "ds_task": ds_task, "ds_on": _ds_on})

# ── CODA ─────────────────────────────────────────────────────
@app.route("/api/coda/add", methods=["POST"])
def coda_add():
    data = request.json or {}
    task = coda_module.add(data.get("type","shell"), data.get("payload","").strip(), data.get("label",""))
    return jsonify(task)

@app.route("/api/coda/add_dual", methods=["POST"])
def coda_add_dual():
    data = request.json or {}
    brief  = data.get("brief","").strip()
    fc     = data.get("file_claude","").strip()
    if not brief or not fc:
        return jsonify({"error": "brief e file_claude obbligatori"}), 400
    label = data.get("label","")[:50]
    write_claude = data.get("write_claude", True)
    autostart    = data.get("autostart", False)   # default standby per code manuali
    task = coda_module.add_dual(label, brief, data.get("output_claude",""), fc,
                                write_claude=write_claude, autostart=autostart)
    log_append("claude", label or fc, f"✓ {task['file_claude']}")
    log_append("deep",   label or fc, f"→ coda: {task['file_deep']} ({'auto' if autostart else 'standby'})")
    return jsonify(task)

@app.route("/api/coda/list")
def coda_list():
    return jsonify(coda_module.list_tasks(request.args.get("status")))

@app.route("/api/coda/get/<task_id>")
def coda_get(task_id):
    t = coda_module.get(task_id)
    return jsonify(t) if t else (jsonify({"error": "non trovato"}), 404)

@app.route("/api/coda/cancel/<task_id>", methods=["POST"])
def coda_cancel(task_id):
    coda_module.cancel(task_id)
    return jsonify({"ok": True})

@app.route("/api/coda/start/<task_id>", methods=["POST"])
def coda_start(task_id):
    coda_module.start_task(task_id)
    return jsonify({"ok": True})

@app.route("/api/coda/clear", methods=["POST"])
def coda_clear():
    coda_module.clear_done()
    log_append("coda", "clear_done", "task completati rimossi dalla coda")
    return jsonify({"ok": True})

# ── Scheduler ────────────────────────────────────────────────
@app.route("/api/scheduler/start", methods=["POST"])
def scheduler_start():
    interval = int((request.json or {}).get("interval", 120))
    sched_module.start(interval)
    log_append("system", "scheduler_start", f"avviato ogni {interval}s")
    return jsonify({"ok": True, "interval": interval})

@app.route("/api/scheduler/stop", methods=["POST"])
def scheduler_stop():
    sched_module.stop()
    return jsonify({"ok": True})

@app.route("/api/scheduler/status")
def scheduler_status():
    return jsonify(sched_module.status())

@app.route("/api/scheduler/topic", methods=["POST"])
def scheduler_topic():
    data  = request.json or {}
    key   = data.get("key", f"topic_{int(time.time())}")
    brief = data.get("brief","").strip()
    if not brief:
        return jsonify({"error": "brief mancante"}), 400
    sched_module.add_topic(key, brief)
    return jsonify({"ok": True})

# ── Shell ────────────────────────────────────────────────────
@app.route("/api/shell", methods=["POST"])
def shell():
    cmd = (request.json or {}).get("cmd","").strip()
    if not cmd:
        return jsonify({"error": "cmd vuoto"}), 400
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                timeout=30, cwd="/home/mattia")
        out = result.stdout + result.stderr
        log_append("system", "shell", cmd[:60])
        return jsonify({"output": out or "(nessun output)", "code": result.returncode})
    except subprocess.TimeoutExpired:
        return jsonify({"output": "[timeout 30s]", "code": -1})
    except Exception as e:
        return jsonify({"output": str(e), "code": -1})

# ── File system ──────────────────────────────────────────────
@app.route("/api/file")
def file_read():
    path = os.path.realpath(request.args.get("path",""))
    if not is_allowed(path):
        return jsonify({"error": "percorso non consentito"}), 403
    if not os.path.isfile(path):
        return jsonify({"error": "non è un file"}), 404
    size = os.path.getsize(path)
    if size > 512 * 1024:
        return jsonify({"error": f"file troppo grande ({size//1024}KB)"}), 400
    try:
        return jsonify({"path": path, "content": open(path, errors="replace").read(), "size": size})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/write_file", methods=["POST"])
def write_file():
    data = request.json or {}
    path = os.path.realpath(data.get("path","").strip())
    if not is_allowed(path):
        return jsonify({"error": "percorso non consentito"}), 403
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(data.get("content",""))
        log_append("write", "write_file", path)
        return jsonify({"ok": True, "path": path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Log ──────────────────────────────────────────────────────
@app.route("/api/log")
def api_log():
    return jsonify(read_log(int(request.args.get("n", 30))))

@app.route("/api/log/add", methods=["POST"])
def api_log_add():
    e = request.json or {}
    log_append(e.get("type",""), e.get("action",""), e.get("detail",""))
    return jsonify({"ok": True})

# ── Report HTML da Python ────────────────────────────────────
@app.route("/api/report", methods=["POST"])
def api_report():
    """
    Genera un file HTML da struttura JSON.
    Body: {
      "title": "Titolo report",
      "filename": "nome_file_claude.html",   (opzionale, default slugify(title))
      "model": "Claude",                      (opzionale)
      "sections": [                           (lista ordinata di blocchi)
        {"type": "h2",      "text": "Sezione"},
        {"type": "p",       "text": "Paragrafo"},
        {"type": "box",     "text": "...", "color": "info"},
        {"type": "table",   "headers": [...], "rows": [...]},
        {"type": "props",   "pairs": [[k,v], ...]},
        {"type": "stats",   "items": [[num,lbl], ...]},
        {"type": "code",    "text": "..."},
        {"type": "list",    "items": [...], "ordered": false},
        {"type": "bar",     "label": "...", "pct": 75, "color": "green"},
        {"type": "html",    "html": "<div>...</div>"}
      ]
    }
    """
    data = request.json or {}
    title    = data.get("title", "Report").strip()
    model    = data.get("model", "Claude")
    filename = data.get("filename", "").strip()
    if not filename:
        import re
        filename = re.sub(r'[^\w]+', '_', title.lower())[:40].strip('_') + "_claude.html"
    path = os.path.join(DIR_CLAUDE, filename)

    blocks = []
    for s in data.get("sections", []):
        t = s.get("type","")
        if   t == "h2":     blocks.append(report_module.h2(s.get("text","")))
        elif t == "h3":     blocks.append(report_module.h3(s.get("text","")))
        elif t == "p":      blocks.append(report_module.p(s.get("text","")))
        elif t == "box":    blocks.append(report_module.box(s.get("text",""), s.get("color","info")))
        elif t == "code":   blocks.append(report_module.code(s.get("text","")))
        elif t == "formula":blocks.append(report_module.formula(s.get("text","")))
        elif t == "table":  blocks.append(report_module.table(s.get("headers",[]), s.get("rows",[])))
        elif t == "props":  blocks.append(report_module.props(s.get("pairs",[])))
        elif t == "stats":  blocks.append(report_module.stat_row(s.get("items",[]), s.get("colors")))
        elif t == "list":
            fn = report_module.list_ol if s.get("ordered") else report_module.list_ul
            blocks.append(fn(s.get("items",[])))
        elif t == "bar":    blocks.append(report_module.bar(s.get("label",""), s.get("pct",0), s.get("color","")))
        elif t == "html":   blocks.append(s.get("html",""))

    html = report_module.make(title, blocks, model)
    report_module.save(html, path)
    log_append("claude", "report", filename)
    return jsonify({"ok": True, "path": path, "filename": filename})

# ── FPS Box ──────────────────────────────────────────────────
@app.route("/api/fpsbox")
def api_fpsbox():
    data = fps_module.get()
    data["running"] = fps_module.is_running()
    return jsonify(data)

@app.route("/api/fpsbox/frame")
def fpsbox_frame():
    frame = fps_module.get_frame()
    if not frame:
        return "", 204
    return app.response_class(frame, mimetype="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

@app.route("/api/fpsbox/start", methods=["POST"])
def api_fpsbox_start():
    fps_module.start()
    return jsonify({"ok": True})

@app.route("/api/fpsbox/stop", methods=["POST"])
def api_fpsbox_stop():
    fps_module.stop()
    return jsonify({"ok": True})

# ── Status ───────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    return jsonify({"status": "online", "service": "SONNET26-LocalAPI", "version": "2.0"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5052, debug=False)
