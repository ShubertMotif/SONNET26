"""
report.py — Generazione HTML per SONNET26.

Uso principale:
  import report
  html = report.make("Titolo", [
      report.stat_row([("42", "task done"), ("3", "standby"), ("0", "errori")]),
      report.h2("Dettaglio"),
      report.table(["Campo","Valore"], [["CPU","34°C"],["RAM","12GB"]]),
      report.box("Tutto ok", "ok"),
  ])
  report.save(html, "/path/file.html")

Usato anche da coda.py per pulire/completare l'output di DeepSonnet26.
"""

import os
import re
import datetime

_TEMPLATE = os.path.join(os.path.dirname(__file__), "../templates/report_base.html")

# ── Helpers interni ──────────────────────────────────────────────────────────

def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

def _load_tpl():
    try:
        with open(_TEMPLATE, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return _MINIMAL_TPL

def _e(s):
    """HTML-escape minimo."""
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

# ── Blocchi HTML ─────────────────────────────────────────────────────────────

def h2(text):
    return f"<h2>{_e(text)}</h2>\n"

def h3(text):
    return f"<h3>{_e(text)}</h3>\n"

def p(text):
    return f"<p>{text}</p>\n"

def box(text, tipo="info"):
    """tipo: info | warn | danger | ok | note"""
    return f'<div class="box box-{tipo}"><p>{text}</p></div>\n'

def code(text, pre=True):
    if pre:
        return f'<div class="code">{_e(text)}</div>\n'
    return f'<code>{_e(text)}</code>'

def formula(text):
    return f'<div class="formula">{_e(text)}</div>\n'

def tag(text, color="blue"):
    return f'<span class="tag tag-{color}">{_e(text)}</span>'

def table(headers, rows, highlight_col=None):
    """
    headers: lista di stringhe
    rows: lista di liste
    highlight_col: indice colonna da evidenziare (classe hl)
    """
    ths = "".join(f"<th>{_e(h)}</th>" for h in headers)
    trs = []
    for row in rows:
        tds = []
        for i, cell in enumerate(row):
            cls = ' class="hl"' if highlight_col is not None and i == highlight_col else ''
            tds.append(f"<td{cls}>{_e(cell)}</td>")
        trs.append(f"<tr>{''.join(tds)}</tr>")
    return f'<table class="tbl"><tr>{ths}</tr>{"".join(trs)}</table>\n'

def props(pairs):
    """pairs: lista di (chiave, valore)"""
    rows = "".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k,v in pairs)
    return f'<table class="props">{rows}</table>\n'

def stat_row(stats, colors=None):
    """
    stats: lista di (numero, label) o (numero, label, sublabel)
    colors: lista di variabili CSS opzionali es. ['var(--green)', ...]
    """
    cards = []
    for i, s in enumerate(stats):
        num, lbl = s[0], s[1]
        sub = s[2] if len(s) > 2 else ""
        col = colors[i] if colors and i < len(colors) else "var(--accent)"
        sub_html = f'<div class="sub">{_e(sub)}</div>' if sub else ""
        cards.append(
            f'<div class="card stat"><div class="num" style="color:{col}">{_e(num)}</div>'
            f'<div class="lbl">{_e(lbl)}</div>{sub_html}</div>'
        )
    return f'<div class="grid">{"".join(cards)}</div>\n'

def list_ul(items):
    lis = "".join(f"<li>{_e(i)}</li>" for i in items)
    return f'<ul class="list">{lis}</ul>\n'

def list_ol(items):
    lis = "".join(f"<li>{_e(i)}</li>" for i in items)
    return f'<ol class="list list-num">{lis}</ol>\n'

def bar(label, pct, color=""):
    col_cls = f' {color}' if color else ''
    return (f'<div style="margin:8px 0"><span style="font-size:.85rem;color:var(--muted)">'
            f'{_e(label)}</span><div style="font-size:.8rem;color:var(--accent);float:right">'
            f'{pct}%</div><div style="clear:both"></div>'
            f'<div class="bar-wrap"><div class="bar{col_cls}" style="width:{pct}%"></div></div></div>\n')

def timeline(events):
    """events: lista di (data, titolo) o (data, titolo, desc)"""
    items = []
    for ev in events:
        dt, title = ev[0], ev[1]
        desc = ev[2] if len(ev) > 2 else ""
        desc_html = f'<div class="tl-desc">{_e(desc)}</div>' if desc else ""
        items.append(
            f'<div class="tl-item"><div class="tl-date">{_e(dt)}</div>'
            f'<div class="tl-title">{_e(title)}</div>{desc_html}</div>'
        )
    return f'<div class="timeline">{"".join(items)}</div>\n'

def grid(*cards_html, cols=None):
    cls = f"grid-{cols}" if cols in (2,3,4) else "grid"
    return f'<div class="{cls}">{"".join(cards_html)}</div>\n'

def card(content_html, color=""):
    cls = f"card card-{color}" if color else "card"
    return f'<div class="{cls}">{content_html}</div>\n'

# ── Assemblaggio report completo ─────────────────────────────────────────────

def make(title, sections, model="Claude", extra_css=""):
    """
    title:    stringa titolo
    sections: stringa HTML già pronta, O lista di stringhe/blocchi
    model:    nome modello per il footer
    Restituisce HTML completo pronto per il file.
    """
    if isinstance(sections, list):
        body = "".join(sections)
    else:
        body = sections

    tpl = _load_tpl()
    # Rimuovi il blocco commento con gli esempi (non serve nel file finale)
    tpl = re.sub(r'<!--\s*═+.*?═+\s*-->', '', tpl, flags=re.DOTALL)

    html = tpl.replace("{{TITOLO}}", _e(title))
    html = html.replace("{{CONTENUTO}}", body)
    html = html.replace("{{MODELLO}}", _e(model))
    html = html.replace("{{DATA}}", _now())
    if extra_css:
        html = html.replace("</style>", extra_css + "\n</style>")
    return html


def save(html, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

# ── Pulizia output DeepSonnet26 ──────────────────────────────────────────────

def fix_output(raw, fallback_title="Report", fallback_model="DeepSonnet26"):
    """
    Pulisce/completa l'output HTML grezzo di DeepSonnet26.
    Gestisce:
      - wrapper markdown ```html ... ```
      - output che inizia con testo prima di <!DOCTYPE
      - HTML incompleto (body/html non chiusi)
      - output che è solo body content senza tag html
    Restituisce sempre HTML valido e completo.
    """
    raw = raw.strip()
    if not raw:
        return make(fallback_title, box("Nessun output ricevuto.", "warn"), fallback_model)

    # 1. Strip markdown fences
    raw = re.sub(r'^```html?\s*\n?', '', raw, flags=re.IGNORECASE | re.MULTILINE)
    raw = re.sub(r'\n?```\s*$', '', raw, flags=re.MULTILINE)
    raw = raw.strip()

    lo = raw.lower()

    # 2. Se c'è <!DOCTYPE o <html>, è HTML strutturato
    if '<!doctype' in lo or '<html' in lo:
        # Elimina testo prima di <!DOCTYPE o <html
        m = re.search(r'(<(!doctype|html)\b)', raw, flags=re.IGNORECASE)
        if m:
            raw = raw[m.start():]
        # Chiudi tag mancanti
        lo = raw.lower()
        if '</body>' not in lo:
            raw = raw.rstrip() + '\n</body>'
        if '</html>' not in lo:
            raw = raw.rstrip() + '\n</html>'
        return raw

    # 3. Output è solo body content → wrappalo nel template
    return make(fallback_title, raw, fallback_model)


# ── Template minimale di emergenza ───────────────────────────────────────────

_MINIMAL_TPL = """<!DOCTYPE html>
<html lang="it"><head><meta charset="UTF-8"><title>{{TITOLO}}</title>
<style>
body{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;padding:24px;max-width:900px;margin:auto}
h1{color:#f0883e;border-bottom:2px solid #f0883e;padding-bottom:8px}
h2{color:#79c0ff;margin:20px 0 8px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px;margin:10px 0}
table{width:100%;border-collapse:collapse}.tbl th,.tbl td{padding:8px;border-bottom:1px solid #30363d}
.tbl th{color:#8b949e}.box{border-left:4px solid;padding:12px;margin:10px 0;border-radius:4px}
.box-info{background:rgba(121,192,255,.08);border-color:#79c0ff}
.box-warn{background:rgba(227,179,65,.08);border-color:#e3b341}
.box-ok{background:rgba(86,211,100,.08);border-color:#56d364}
footer{margin-top:24px;border-top:1px solid #30363d;padding-top:10px;font-size:.8rem;color:#484f58}
</style></head><body>
<h1>{{TITOLO}}</h1>
{{CONTENUTO}}
<footer><span>{{MODELLO}} — {{DATA}}</span><span>SONNET26</span></footer>
</body></html>"""
