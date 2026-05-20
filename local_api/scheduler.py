import threading
import time
import datetime
import json
import os
import requests

import coda as coda_module

DIR_CLAUDE       = "/home/mattia/Scrivania/SONNET26/output_claude"
OLLAMA_URL       = "http://localhost:11434"
MODEL            = "deepsonnet26"
SCHED_STATE_FILE = os.path.join(os.path.dirname(__file__), "../data/sched_state.json")

# Lista topic da ricercare — popolabile a runtime
DEFAULT_TOPICS = [
    ("montagne_mondo",    "Crea un JSON con le 10 montagne più alte del mondo: nome, altezza_m, paese, catena_montuosa, prima_salita_anno."),
    ("fiumi_africa",      "Crea un JSON con i 10 fiumi più lunghi dell'Africa: nome, lunghezza_km, paesi_attraversati, sfocia_in."),
    ("inventori_900",     "Crea un JSON con 10 inventori fondamentali del XX secolo: nome, nazionalità, invenzione, anno, impatto."),
    ("lingue_mondo",      "Crea un JSON con le 10 lingue più parlate al mondo: lingua, madrelingua_milioni, totale_parlanti_milioni, famiglia_linguistica."),
    ("animali_estinzione","Crea un JSON con 10 animali criticamente in via di estinzione: nome, nome_scientifico, popolazione_stimata, habitat, causa_principale."),
    ("opere_arte",        "Crea un JSON con 10 opere d'arte più famose al mondo: titolo, autore, anno, museo, tecnica, valore_stimato_mln_usd."),
    ("pianeti_sistema",   "Crea un JSON con i 8 pianeti del sistema solare: nome, distanza_sole_ua, diametro_km, satelliti, temperatura_media_c, caratteristica_unica."),
    ("battaglie_storia",  "Crea un JSON con 10 battaglie decisive della storia: nome, anno, vincitori, perdenti, luogo, conseguenza_storica."),
    ("ceo_tech",          "Crea un JSON con 10 CEO più influenti della tech mondiale: nome, azienda, fatturato_mld_usd, anno_fondazione, prodotto_icona."),
    ("vulcani_attivi",    "Crea un JSON con i 10 vulcani più pericolosi del mondo: nome, paese, ultima_eruzione, tipo, rischio_livello."),
]

_state = {
    "running":   False,
    "interval":  120,        # secondi tra un task e l'altro
    "topics":    list(DEFAULT_TOPICS),
    "index":     0,
    "current":   None,
    "next_at":   None,
    "done":      0,
    "thread":    None,
}
_lock = threading.Lock()


def _save_sched_state():
    try:
        os.makedirs(os.path.dirname(SCHED_STATE_FILE), exist_ok=True)
        with _lock:
            s = {"index": _state["index"], "done": _state["done"]}
        with open(SCHED_STATE_FILE, "w") as f:
            json.dump(s, f)
    except Exception:
        pass

def _load_sched_state():
    try:
        with open(SCHED_STATE_FILE) as f:
            s = json.load(f)
        with _lock:
            _state["index"] = s.get("index", 0)
            _state["done"]  = s.get("done", 0)
    except Exception:
        pass

_load_sched_state()

def status():
    with _lock:
        s = {k: v for k, v in _state.items() if k != "thread"}
        s["topics_left"] = len(_state["topics"]) - _state["index"]
    return s


def start(interval=120):
    with _lock:
        if _state["running"]:
            return
        # Reset se topic esauriti
        if _state["index"] >= len(_state["topics"]):
            _state["index"] = 0
            _state["done"]  = 0
        _state["running"]  = True
        _state["interval"] = interval
        _state["next_at"]  = _now_ts() + interval
    t = threading.Thread(target=_loop, daemon=True)
    with _lock:
        _state["thread"] = t
    t.start()
    _log("scheduler_start", f"avviato — intervallo {interval}s, {len(DEFAULT_TOPICS)} topic")


def stop():
    with _lock:
        _state["running"] = False
        _state["next_at"] = None
    _log("scheduler_stop", "scheduler fermato")


def add_topic(key, brief):
    with _lock:
        _state["topics"].append((key, brief))
    _log("scheduler_topic", f"aggiunto topic: {key}")


def _now_ts():
    return int(time.time())


def _now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(action, detail, type_="sched"):
    try:
        requests.post("http://localhost:5052/api/log/add", json={
            "type": type_, "action": action, "detail": detail
        }, timeout=2)
    except Exception:
        pass


def _ollama_generate(prompt, system):
    """Chiama Ollama direttamente con streaming, ritorna testo completo."""
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "stream": True,
        }, stream=True, timeout=300)
        out = ""
        for line in r.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
                out += chunk.get("message", {}).get("content", "")
                if chunk.get("done"):
                    break
            except Exception:
                continue
        return out.strip()
    except Exception as e:
        return f"[errore ollama: {e}]"


def _process_topic(key, brief):
    """Esegue un ciclo completo: Claude (Ollama sys1) + DeepSonnet (CODA)."""
    with _lock:
        _state["current"] = {"key": key, "brief": brief[:80], "started": _now_str()}

    _log(key.replace("_", " "), "generazione avviata")

    # ── CLAUDE (Ollama con system prompt ricercatore) ──────────
    sys_claude = (
        "Sei un assistente ricerca preciso. Rispondi SOLO con JSON valido, "
        "senza markdown, senza spiegazioni. Array o oggetto, dati accurati in italiano."
    )
    output_claude = _ollama_generate(brief, sys_claude)

    # Pulizia markdown se presente
    for fence in ["```json", "```"]:
        if output_claude.startswith(fence):
            output_claude = output_claude[len(fence):]
    output_claude = output_claude.rstrip("`").strip()

    # Salva file Claude
    os.makedirs(DIR_CLAUDE, exist_ok=True)
    fname_claude = f"{key}_{datetime.datetime.now().strftime('%H%M%S')}_claude.html"
    path_claude  = os.path.join(DIR_CLAUDE, fname_claude)
    with open(path_claude, "w", encoding="utf-8") as f:
        f.write(output_claude)

    # ── DEEPSONNET via CODA ────────────────────────────────────
    task = coda_module.add_dual(
        label        = key.replace("_", " "),
        brief        = brief,
        output_claude= output_claude,
        file_claude  = fname_claude,
        write_claude = False,
    )

    _log(key.replace("_", " "), f"✓ {fname_claude}", type_="claude")
    _log(key.replace("_", " "), f"→ coda: {task['file_deep']}", type_="deep")

    with _lock:
        _state["done"]    += 1
        _state["current"]  = None
    _save_sched_state()


def _loop():
    while True:
        with _lock:
            if not _state["running"]:
                break
            topics  = _state["topics"]
            idx     = _state["index"]
            next_at = _state["next_at"]

        now = _now_ts()

        if now >= next_at:
            if idx < len(topics):
                key, brief = topics[idx]
                with _lock:
                    _state["index"]   = idx + 1
                    _state["next_at"] = _now_ts() + _state["interval"]
                _process_topic(key, brief)
            else:
                # Lista esaurita — ferma
                with _lock:
                    _state["running"] = False
                _log("scheduler_end", "tutti i topic completati")
                break

        time.sleep(3)
