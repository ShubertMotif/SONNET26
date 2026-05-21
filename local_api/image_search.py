"""
image_search.py — Ricerca e download immagini per report HTML SONNET26.
Sorgenti gratuite senza API key:
  1. Wikipedia REST API  (pagina principale del topic)
  2. Wikimedia Commons   (fallback per query generiche)
Architettura pronta per Google Custom Search (slot config in identita.json).
"""
import os, re, requests

TIMEOUT  = 8
IMG_MAX  = 3
_UA      = "SONNET26/1.0 (report-generator; https://github.com/ShubertMotif/SONNET26)"

# ── Wikipedia REST API ────────────────────────────────────────────────────────

def _wikipedia(query):
    title = query.strip().replace(' ', '_')
    for lang in ('it', 'en'):
        try:
            r = requests.get(
                f'https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}',
                timeout=TIMEOUT, headers={'User-Agent': _UA}
            )
            if r.status_code != 200:
                continue
            d     = r.json()
            thumb = d.get('thumbnail', {}).get('source', '')
            if not thumb:
                continue
            return [{'url': thumb, 'title': d.get('title', query),
                     'src': f'Wikipedia ({lang})'}]
        except Exception:
            pass
    return []

# ── Wikimedia Commons search ──────────────────────────────────────────────────

def _wikimedia(query, n=IMG_MAX):
    try:
        r = requests.get(
            'https://commons.wikimedia.org/w/api.php',
            params={
                'action': 'query', 'generator': 'search',
                'gsrsearch': f'File:{query}', 'gsrnamespace': 6,
                'gsrlimit': n, 'prop': 'imageinfo',
                'iiprop': 'url', 'iiurlwidth': 400, 'format': 'json',
            },
            timeout=TIMEOUT, headers={'User-Agent': _UA}
        )
        if r.status_code != 200:
            return []
        pages = r.json().get('query', {}).get('pages', {}).values()
        out = []
        for p in pages:
            ii = p.get('imageinfo', [{}])[0]
            url = ii.get('thumburl') or ii.get('url', '')
            if url:
                out.append({'url': url,
                            'title': p.get('title','').replace('File:',''),
                            'src': 'Wikimedia Commons'})
        return out
    except Exception:
        return []

# ── Pubblico ──────────────────────────────────────────────────────────────────

def search_images(query, n=IMG_MAX):
    """Cerca n immagini: Wikipedia prima, Wikimedia fallback."""
    imgs = _wikipedia(query)
    if not imgs:
        imgs = _wikimedia(query, n)
    return imgs[:n]

def _fname_slug(text):
    return re.sub(r'[^\w]+', '_', text.lower().strip())[:30].strip('_')

def download_images(images, output_dir, slug):
    """
    Scarica in output_dir/img/<slug>__<titolo_immagine>_N.ext
    Nome file searchable: contiene sia il topic che il titolo Wikipedia.
    Restituisce lista con 'rel_path' aggiunto a ogni dict.
    """
    img_dir = os.path.join(output_dir, 'img')
    os.makedirs(img_dir, exist_ok=True)
    result = []
    for i, img in enumerate(images):
        url = img.get('url', '')
        if not url:
            continue
        ext = url.split('?')[0].rsplit('.', 1)[-1].lower()
        if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'):
            ext = 'jpg'
        title_part = _fname_slug(img.get('title', ''))
        fname = f'{slug}__{title_part}_{i}.{ext}' if title_part else f'{slug}_{i}.{ext}'
        local = os.path.join(img_dir, fname)
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={'User-Agent': _UA})
            if r.status_code == 200:
                with open(local, 'wb') as f:
                    f.write(r.content)
                result.append({**img, 'rel_path': f'img/{fname}', 'local_path': local})
        except Exception:
            pass
    return result

def fetch_for_report(query, output_dir, slug, n=IMG_MAX):
    """All-in-one: cerca + scarica. Ritorna [] se nulla trovato o errore."""
    try:
        imgs = search_images(query, n)
        if not imgs:
            return []
        return download_images(imgs, output_dir, slug)
    except Exception:
        return []
