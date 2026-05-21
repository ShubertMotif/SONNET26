"""
db.py — SQLite persistence per SONNET26.
Sessions raggruppano task logicamente (enciclopedia, trading, drone, ecc.)
Tasks: unità di lavoro dual — Claude (Anthropic API) + DeepSonnet26 (Ollama/RTX3060)
"""
import sqlite3, uuid, datetime, os, re, threading

DB_PATH = os.path.join(os.path.dirname(__file__), "../data/sonnet26.db")
_lock   = threading.Lock()

def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c

def init():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id      TEXT PRIMARY KEY,
                label   TEXT NOT NULL,
                status  TEXT DEFAULT 'active',
                created TEXT NOT NULL,
                updated TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id              TEXT PRIMARY KEY,
                session_id      TEXT NOT NULL,
                label           TEXT NOT NULL,
                brief           TEXT NOT NULL,
                status          TEXT DEFAULT 'pending',
                worker_claude   TEXT DEFAULT 'pending',
                worker_deep     TEXT DEFAULT 'pending',
                file_claude     TEXT DEFAULT '',
                file_deep       TEXT DEFAULT '',
                path_claude     TEXT DEFAULT '',
                path_deep       TEXT DEFAULT '',
                output_claude   TEXT DEFAULT '',
                output_deep     TEXT DEFAULT '',
                toks_s          REAL    DEFAULT 0,
                priority        INTEGER DEFAULT 0,
                created         TEXT NOT NULL,
                started         TEXT DEFAULT '',
                started_claude  TEXT DEFAULT '',
                finished_claude TEXT DEFAULT '',
                started_deep    TEXT DEFAULT '',
                finished_deep   TEXT DEFAULT '',
                finished        TEXT DEFAULT ''
            );
        """)

init()

# ── helpers ──────────────────────────────────────────────────────────────────

def slugify(text):
    return re.sub(r'[^\w]+', '_', text.lower().strip())[:40].strip('_') or 'item'

# ── Sessions ─────────────────────────────────────────────────────────────────

def session_create(label, sid=None):
    sid = sid or slugify(label)
    now = _now()
    with _lock:
        with _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO sessions (id,label,status,created,updated) VALUES (?,?,?,?,?)",
                (sid, label, 'active', now, now)
            )
    return session_get(sid)

def session_get(sid):
    with _conn() as c:
        row = c.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row: return None
        d = dict(row)
        s = c.execute("""
            SELECT
                COUNT(*)                        total,
                SUM(status='done')              done,
                SUM(status='pending')           pending,
                SUM(status='running')           running,
                SUM(status='error')             error,
                SUM(status='cancelled')         cancelled,
                SUM(worker_deep='running')      deep_running,
                SUM(worker_claude='running')    claude_running,
                SUM(worker_deep='done')         deep_done
            FROM tasks WHERE session_id=?
        """, (sid,)).fetchone()
        d['stats'] = dict(s) if s else {}
        return d

def session_list():
    with _conn() as c:
        ids = [r['id'] for r in c.execute("SELECT id FROM sessions ORDER BY created DESC").fetchall()]
    return [session_get(i) for i in ids]

def session_update(sid, **kw):
    if not kw: return
    sets = ', '.join(f"{k}=?" for k in kw)
    vals = list(kw.values()) + [_now(), sid]
    with _lock:
        with _conn() as c:
            c.execute(f"UPDATE sessions SET {sets}, updated=? WHERE id=?", vals)

# ── Tasks ─────────────────────────────────────────────────────────────────────

def task_add(session_id, label, brief, path_claude, path_deep,
             file_claude, file_deep, priority=0):
    tid = str(uuid.uuid4())[:8]
    now = _now()
    with _lock:
        with _conn() as c:
            c.execute("""
                INSERT INTO tasks
                (id, session_id, label, brief, status,
                 worker_claude, worker_deep,
                 file_claude, file_deep, path_claude, path_deep,
                 priority, created)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (tid, session_id, label, brief, 'pending',
                  'pending', 'pending',
                  file_claude, file_deep, path_claude, path_deep,
                  priority, now))
            c.execute("UPDATE sessions SET updated=? WHERE id=?", (now, session_id))
    return task_get(tid)

def task_get(tid):
    with _conn() as c:
        row = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None

def task_list(session_id=None, status=None, limit=200):
    q, p = "SELECT * FROM tasks WHERE 1=1", []
    if session_id: q += " AND session_id=?"; p.append(session_id)
    if status:     q += " AND status=?";     p.append(status)
    q += " ORDER BY priority ASC, created ASC LIMIT ?"; p.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(q, p).fetchall()]

def task_next_deep():
    """Prossimo task deep in attesa, per priorità poi FIFO."""
    with _conn() as c:
        row = c.execute("""
            SELECT * FROM tasks
            WHERE worker_deep='pending' AND status='pending'
            ORDER BY priority ASC, created ASC LIMIT 1
        """).fetchone()
        return dict(row) if row else None

def task_next_claude():
    """Claude parte appena deep è done o error (esegue comunque)."""
    with _conn() as c:
        row = c.execute("""
            SELECT * FROM tasks
            WHERE worker_claude='pending' AND worker_deep IN ('done','error')
            ORDER BY priority ASC, created ASC LIMIT 1
        """).fetchone()
        return dict(row) if row else None

def task_deep_running():
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM tasks WHERE worker_deep='running'"
        ).fetchone()[0] > 0

def task_get_deep_running():
    """Restituisce il task con worker_deep='running', o None."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM tasks WHERE worker_deep='running' LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

def task_force_error(tid):
    """Forza un task a error (usato dal watchdog per task bloccati)."""
    now = _now()
    with _lock:
        with _conn() as c:
            c.execute(
                "UPDATE tasks SET status='error', worker_deep='error', "
                "finished_deep=?, finished=? WHERE id=?",
                (now, now, tid)
            )

def task_set_deep(tid, status, output="", toks_s=0):
    now = _now()
    with _lock:
        with _conn() as c:
            if status == 'running':
                c.execute("""UPDATE tasks SET worker_deep='running',
                             started_deep=?, started=?, status='running'
                             WHERE id=?""", (now, now, tid))
            else:
                task_status = 'running' if status == 'done' else 'error'
                c.execute("""UPDATE tasks SET worker_deep=?, output_deep=?,
                             toks_s=?, finished_deep=?, status=?
                             WHERE id=?""",
                          (status, output[:3000], toks_s, now, task_status, tid))
                c.execute("UPDATE sessions SET updated=? WHERE id="
                          "(SELECT session_id FROM tasks WHERE id=?)", (now, tid))

def task_set_claude(tid, status, output=""):
    now = _now()
    with _lock:
        with _conn() as c:
            if status == 'running':
                c.execute("UPDATE tasks SET worker_claude='running', started_claude=? WHERE id=?",
                          (now, tid))
            else:
                c.execute("""UPDATE tasks SET worker_claude=?, output_claude=?,
                             finished_claude=?, status='done', finished=?
                             WHERE id=?""",
                          (status, output[:3000], now, now, tid))
                c.execute("UPDATE sessions SET updated=? WHERE id="
                          "(SELECT session_id FROM tasks WHERE id=?)", (now, tid))

def task_cancel(tid):
    with _lock:
        with _conn() as c:
            c.execute("""UPDATE tasks SET status='cancelled',
                         worker_deep='cancelled', worker_claude='cancelled'
                         WHERE id=? AND status IN ('pending','running')""", (tid,))

def task_cancel_session(sid):
    with _lock:
        with _conn() as c:
            c.execute("""UPDATE tasks SET status='cancelled',
                         worker_deep='cancelled', worker_claude='cancelled'
                         WHERE session_id=? AND status IN ('pending','running')""", (sid,))

def task_reset_running():
    """All'avvio del worker: resetta running orfani → pending (da restart API)."""
    with _lock:
        with _conn() as c:
            c.execute("UPDATE tasks SET worker_claude='pending' WHERE worker_claude='running'")
            c.execute("UPDATE tasks SET worker_deep='pending',  status='pending' WHERE worker_deep='running'")

def task_clear_done(session_id=None):
    with _lock:
        with _conn() as c:
            if session_id:
                c.execute("DELETE FROM tasks WHERE session_id=? AND status IN ('done','cancelled','error')",
                          (session_id,))
            else:
                c.execute("DELETE FROM tasks WHERE status IN ('done','cancelled','error')")
