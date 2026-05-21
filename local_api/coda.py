"""
coda.py — Worker loop SONNET26.
Claude  → Anthropic API reale  (cloud)
DeepSonnet26 → Ollama locale   (RTX 3060)
Persistenza su SQLite via db.py
"""
import threading, time, os, json, datetime, requests
import anthropic
import db as _db
import report as _report
import fps_monitor as _fps

# ── Identità ─────────────────────────────────────────────────────────────────
_ID_FILE = os.path.join(os.path.dirname(__file__), "../data/identita.json")

def _load_id():
    try:
        with open(_ID_FILE) as f: return json.load(f)
    except: return {}

_ID = _load_id()

def _api_key():
    kf = (_ID.get("claude") or {}).get("api_key_file", "")
    if not kf:
        try:
            with open(_ID_FILE) as f:
                data = json.load(f)
            kf = (data.get("claude") or {}).get("api_key_file", "")
        except Exception:
            pass
    if kf:
        try:
            with open(kf) as f: return f.read().strip()
        except Exception:
            pass
    return os.environ.get("ANTHROPIC_API_KEY", "")

CLAUDE_MODEL  = (_ID.get("claude")       or {}).get("modello",   "claude-sonnet-4-6")
DEEP_ENDPOINT = (_ID.get("deepsonnet26") or {}).get("endpoint",  "http://localhost:11434/api/chat")
DEEP_MODEL    = (_ID.get("deepsonnet26") or {}).get("modello",   "deepsonnet26")
DIR_CLAUDE    = (_ID.get("claude")       or {}).get("output_dir","/home/mattia/Scrivania/SONNET26/output_claude")
DIR_DEEP      = (_ID.get("deepsonnet26") or {}).get("output_dir","/mnt/sda3/SONNET26_DATA/output_deep")

# ── Template CSS per i prompt ─────────────────────────────────────────────────
_TPL_FILE = os.path.join(os.path.dirname(__file__), "../templates/report_base.html")

def _load_tpl():
    try:
        with open(_TPL_FILE, encoding="utf-8") as f: return f.read()
    except: return ""

_TPL = _load_tpl()

_DEEP_SYSTEM = """Sei DeepSonnet26, AI locale di Adelchi Group SRLS su RTX 3060.
Generi report HTML completi in italiano. REGOLA: rispondi SOLO con HTML valido.

USA QUESTO TEMPLATE BASE (CSS già incluso):
""" + _TPL + """
ISTRUZIONI:
- Sostituisci {{TITOLO}}, {{CONTENUTO}}, {{MODELLO}}=DeepSonnet26, {{DATA}}
- Classi disponibili: .card .grid .tbl .props .stat .box-info .box-ok .box-warn .box-danger .list .formula .bar-wrap .timeline
- Sezioni logiche h2/h3, dati precisi, layout compatto
- Aggiungi prospettiva tecnica che Claude non ha (calcoli, alternative, errori corretti)
- Output: HTML completo autosufficiente"""

_CLAUDE_SYSTEM = """Sei Claude (Anthropic). Generi HTML compatto in italiano — risposta diretta, 2-3 sezioni max.
REGOLA ASSOLUTA: solo HTML valido, niente testo fuori da <html>...</html>.

STRUTTURA OBBLIGATORIA (copia esatta, sostituisci solo i segnaposto):
<!DOCTYPE html><html lang="it"><head><meta charset="UTF-8">
<title>TITOLO</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;font-size:14px;line-height:1.6;padding:24px 28px;max-width:900px;margin:0 auto}
h1{font-size:1.5rem;color:#f0883e;border-bottom:2px solid #f0883e;padding-bottom:8px;margin-bottom:20px}
h2{font-size:1.05rem;color:#79c0ff;margin:20px 0 10px;border-left:3px solid #79c0ff;padding-left:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;margin:14px 0}
.card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:14px}
.card-blue{border-color:#79c0ff} .card-green{border-color:#56d364} .card-red{border-color:#ff7b72}
p{margin:6px 0} strong{color:#f0883e} ul{padding-left:18px;margin:6px 0} li{margin:3px 0;color:#8b949e}
footer{margin-top:24px;padding-top:10px;border-top:1px solid #30363d;font-size:.75rem;color:#484f58}
</style></head><body>
<h1>TITOLO</h1>
CONTENUTO
<footer>Claude · DATA</footer>
</body></html>

ISTRUZIONI:
- Compila TITOLO, CONTENUTO, DATA (oggi)
- CONTENUTO = 2-3 .card con analisi critica, dati chiave, angolo diverso da Deep
- Niente strutture lunghe — vai dritto al punto"""

# ── Helpers paths ─────────────────────────────────────────────────────────────

def _slugify(text):
    import re
    return re.sub(r'[^\w]+', '_', text.lower().strip())[:40].strip('_') or 'task'

def _make_paths(label):
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slugify(label)
    fc   = f"{slug}_{ts}_claude.html"
    fd   = f"{slug}_{ts}_deepsonnet26.html"
    return fc, fd, os.path.join(DIR_CLAUDE, fc), os.path.join(DIR_DEEP, fd)

# ── Public API ────────────────────────────────────────────────────────────────

def session_create(label, sid=None):
    """Crea (o recupera) una sessione di lavoro."""
    return _db.session_create(label, sid)

def session_add_tasks(session_id, tasks):
    """
    Aggiunge lista di task a una sessione.
    tasks = [{"label": "...", "brief": "..."}, ...]
    Restituisce lista task creati.
    """
    created = []
    for i, t in enumerate(tasks):
        label = t.get("label", f"task_{i+1}")
        brief = t.get("brief", label)
        fc, fd, pc, pd = _make_paths(label)
        task = _db.task_add(session_id, label, brief, pc, pd, fc, fd, priority=i)
        created.append(task)
    _ensure_worker()
    return created

def task_get(tid):
    return _db.task_get(tid)

def task_list(session_id=None, status=None):
    return _db.task_list(session_id=session_id, status=status)

list_tasks = task_list  # alias usato da app.py dashboard

def session_get(sid):
    return _db.session_get(sid)

def session_list():
    return _db.session_list()

def task_cancel(tid):
    _db.task_cancel(tid)

def session_cancel(sid):
    _db.task_cancel_session(sid)

def clear_done(session_id=None):
    _db.task_clear_done(session_id)

# ── Worker: DeepSonnet26 (Ollama / RTX 3060) ─────────────────────────────────

def _run_deep(task):
    _fps.set_deep_active(True)
    _db.task_set_deep(task["id"], "running")
    try:
        r = requests.post(
            DEEP_ENDPOINT,
            json={
                "model": DEEP_MODEL,
                "messages": [
                    {"role": "system", "content": _DEEP_SYSTEM},
                    {"role": "user",   "content": task["brief"]},
                ],
                "stream": True,
            },
            stream=True, timeout=300,
        )
        accumulated = ""
        tok_count   = 0
        t_start     = time.time()

        for line in r.iter_lines():
            if not line: continue
            try:
                chunk = json.loads(line)
                tok   = chunk.get("message", {}).get("content", "")
                if tok:
                    accumulated += tok
                    tok_count   += 1
                if chunk.get("done"): break
            except Exception: continue

        elapsed = time.time() - t_start
        toks_s  = round(tok_count / elapsed, 1) if elapsed > 0 else 0

        cleaned = _report.fix_output(accumulated,
                                     fallback_title=task["label"],
                                     fallback_model="DeepSonnet26")
        os.makedirs(DIR_DEEP, exist_ok=True)
        with open(task["path_deep"], "w", encoding="utf-8") as f:
            f.write(cleaned)

        _db.task_set_deep(task["id"], "done",
                          output=accumulated[:3000], toks_s=toks_s)
        _log_api("deep", task["label"], f"✓ {task['file_deep']} ({toks_s} tok/s)")

    except Exception as e:
        _db.task_set_deep(task["id"], "error", output=str(e))
        _log_api("deep", task["label"], f"✗ {str(e)[:80]}")
    finally:
        _fps.set_deep_active(False)

# ── Worker: Claude (Anthropic API reale) ──────────────────────────────────────

def _run_claude(task):
    _db.task_set_claude(task["id"], "running")
    try:
        client = anthropic.Anthropic(api_key=_api_key())
        accumulated = ""
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=1200,
            system=_CLAUDE_SYSTEM,
            messages=[{"role": "user", "content": task["brief"]}],
        ) as stream:
            for text in stream.text_stream:
                accumulated += text

        cleaned = _report.fix_output(accumulated,
                                     fallback_title=task["label"],
                                     fallback_model="Claude")
        os.makedirs(DIR_CLAUDE, exist_ok=True)
        with open(task["path_claude"], "w", encoding="utf-8") as f:
            f.write(cleaned)

        _db.task_set_claude(task["id"], "done", output=accumulated[:3000])
        _log_api("claude", task["label"], f"✓ {task['file_claude']}")

    except Exception as e:
        _db.task_set_claude(task["id"], "error", output=str(e))
        _log_api("claude", task["label"], f"✗ {str(e)[:80]}")

# ── Worker loop ───────────────────────────────────────────────────────────────

def _worker_loop():
    while True:
        # Priorità 1: deep (RTX) — uno alla volta
        if not _db.task_deep_running():
            task = _db.task_next_deep()
            if task:
                _run_deep(task)
                continue

        # Priorità 2: claude — parte appena il suo deep è done, in parallelo con Deep
        task = _db.task_next_claude()
        if task:
            _run_claude(task)
            continue

        time.sleep(2)

_worker_thread = None

def _ensure_worker():
    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_worker_loop, daemon=True)
        _worker_thread.start()

_ensure_worker()

# ── Log bridge → app.py ───────────────────────────────────────────────────────

def _log_api(tipo, action, detail=""):
    try:
        requests.post("http://localhost:5052/api/log/add",
                      json={"type": tipo, "action": action, "detail": detail},
                      timeout=2)
    except Exception:
        pass
