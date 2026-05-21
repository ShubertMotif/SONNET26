"""
image_search.py — Ricerca e download immagini per report HTML SONNET26.
Sorgenti gratuite senza API key:
  1. Wikipedia REST API     — immagine principale del topic (it/en)
  2. Wikimedia Commons      — ricerca per topic, immagini multiple
  3. DuckDuckGo Instant     — immagine di sintesi per topic generici
  4. The Met Museum API     — arte/storia/archeologia (open access)
Slot config pronto per Google Custom Search in identita.json.
"""
import os, re, requests

TIMEOUT = 8
IMG_MAX = 3
_UA     = "SONNET26/1.0 (report-generator; https://github.com/ShubertMotif/SONNET26)"

# ── 1. Wikipedia REST API ─────────────────────────────────────────────────────

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
            d = r.json()
            thumb = d.get('thumbnail', {}).get('source', '')
            if not thumb:
                continue
            return [{'url': thumb, 'title': d.get('title', query),
                     'src': f'Wikipedia ({lang})'}]
        except Exception:
            pass
    return []

# ── 2. Wikimedia Commons ──────────────────────────────────────────────────────

def _wikimedia(query, n=IMG_MAX):
    try:
        r = requests.get(
            'https://commons.wikimedia.org/w/api.php',
            params={
                'action': 'query', 'generator': 'search',
                'gsrsearch': f'File:{query}', 'gsrnamespace': 6,
                'gsrlimit': n + 3,          # ne chiedo extra per dedup
                'prop': 'imageinfo',
                'iiprop': 'url|mime', 'iiurlwidth': 500, 'format': 'json',
            },
            timeout=TIMEOUT, headers={'User-Agent': _UA}
        )
        if r.status_code != 200:
            return []
        pages = r.json().get('query', {}).get('pages', {}).values()
        out = []
        for p in pages:
            ii  = p.get('imageinfo', [{}])[0]
            url = ii.get('thumburl') or ii.get('url', '')
            mime = ii.get('mime', '')
            if url and 'svg' not in mime:       # salta SVG (spesso non foto)
                out.append({'url': url,
                            'title': p.get('title', '').replace('File:', ''),
                            'src': 'Wikimedia Commons'})
        return out[:n]
    except Exception:
        return []

# ── 3. DuckDuckGo Instant Answer ──────────────────────────────────────────────

def _duckduckgo(query):
    try:
        r = requests.get(
            'https://api.duckduckgo.com/',
            params={'q': query, 'format': 'json', 'no_html': 1, 'skip_disambig': 1},
            timeout=TIMEOUT, headers={'User-Agent': _UA}
        )
        if r.status_code != 200:
            return []
        d   = r.json()
        url = d.get('Image', '')
        if url and url.startswith('http'):
            return [{'url': url, 'title': d.get('Heading', query),
                     'src': 'DuckDuckGo'}]
    except Exception:
        pass
    return []

# ── 4. The Met Museum (open access) ──────────────────────────────────────────

def _met_museum(query, n=2):
    try:
        r = requests.get(
            'https://collectionapi.metmuseum.org/public/collection/v1/search',
            params={'q': query, 'hasImages': 'true'},
            timeout=TIMEOUT, headers={'User-Agent': _UA}
        )
        if r.status_code != 200:
            return []
        ids = r.json().get('objectIDs') or []
        out = []
        for oid in ids[:n * 3]:             # tenta più oggetti (non tutti hanno img pubblica)
            try:
                obj = requests.get(
                    f'https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}',
                    timeout=TIMEOUT, headers={'User-Agent': _UA}
                ).json()
                url = obj.get('primaryImageSmall') or obj.get('primaryImage', '')
                if url:
                    out.append({'url': url,
                                'title': obj.get('title', query),
                                'src': 'The Met Museum'})
                if len(out) >= n:
                    break
            except Exception:
                continue
        return out
    except Exception:
        return []

# ── Dedup helper ──────────────────────────────────────────────────────────────

def _dedup(imgs):
    seen, out = set(), []
    for i in imgs:
        if i['url'] not in seen:
            seen.add(i['url'])
            out.append(i)
    return out

# ── Pubblico ──────────────────────────────────────────────────────────────────

def search_images(query, n=IMG_MAX):
    """
    Raccoglie fino a n immagini da più sorgenti in parallelo:
    Wikipedia → Wikimedia Commons → DuckDuckGo → Met Museum
    """
    imgs = []

    # Wikipedia: immagine principale (1)
    imgs.extend(_wikipedia(query))

    # Wikimedia Commons: riempie fino a n
    if len(imgs) < n:
        imgs.extend(_wikimedia(query, n - len(imgs)))
        imgs = _dedup(imgs)

    # DuckDuckGo: se ancora sotto
    if len(imgs) < n:
        imgs.extend(_duckduckgo(query))
        imgs = _dedup(imgs)

    # Met Museum: per topic arte/storia
    if len(imgs) < n:
        imgs.extend(_met_museum(query, n - len(imgs)))
        imgs = _dedup(imgs)

    return imgs[:n]

# ── Download ──────────────────────────────────────────────────────────────────

def _fname_slug(text):
    return re.sub(r'[^\w]+', '_', text.lower().strip())[:30].strip('_')

def download_images(images, output_dir, slug):
    """
    Scarica in output_dir/img/<slug>__<titolo>_N.ext
    Nome searchable: topic + titolo immagine + indice.
    """
    img_dir = os.path.join(output_dir, 'img')
    os.makedirs(img_dir, exist_ok=True)
    result = []
    for i, img in enumerate(images):
        url = img.get('url', '')
        if not url:
            continue
        ext = url.split('?')[0].rsplit('.', 1)[-1].lower()
        if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
            ext = 'jpg'
        title_part = _fname_slug(img.get('title', ''))
        fname = f'{slug}__{title_part}_{i}.{ext}' if title_part else f'{slug}_{i}.{ext}'
        local = os.path.join(img_dir, fname)
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={'User-Agent': _UA})
            if r.status_code == 200 and len(r.content) > 1000:
                with open(local, 'wb') as f:
                    f.write(r.content)
                result.append({**img, 'rel_path': f'img/{fname}', 'local_path': local})
        except Exception:
            pass
    return result

def fetch_for_report(query, output_dir, slug, n=IMG_MAX):
    """All-in-one: cerca + scarica. Ritorna [] se errore o nulla trovato."""
    try:
        imgs = search_images(query, n)
        if not imgs:
            return []
        return download_images(imgs, output_dir, slug)
    except Exception:
        return []
