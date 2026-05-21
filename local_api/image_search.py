"""
image_search.py — Ricerca immagini Wikipedia per report HTML SONNET26.
Sorgente: Wikipedia REST API + MediaWiki API (no key, gratuito).
Slot pronto per Google Custom Search (config in identita.json).
"""
import os, re, requests

TIMEOUT = 8
IMG_MAX = 3
_UA     = "SONNET26/1.0 (report-generator; https://github.com/ShubertMotif/SONNET26)"

# ── Wikipedia ─────────────────────────────────────────────────────────────────

def _wiki_images(query, n=IMG_MAX):
    """
    Recupera fino a n immagini dall'articolo Wikipedia (it poi en).
    1. REST summary → thumbnail principale
    2. MediaWiki API → lista immagini pagina → URL via imageinfo
    """
    title = query.strip().replace(' ', '_')
    for lang in ('it', 'en'):
        results = []
        base_mw = f'https://{lang}.wikipedia.org/w/api.php'

        # 1. Thumbnail principale dalla REST summary
        try:
            r = requests.get(
                f'https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}',
                timeout=TIMEOUT, headers={'User-Agent': _UA}
            )
            if r.status_code == 200:
                d = r.json()
                thumb = d.get('thumbnail', {}).get('source', '')
                if thumb:
                    results.append({'url': thumb,
                                    'title': d.get('title', query),
                                    'src': f'Wikipedia ({lang})'})
        except Exception:
            pass

        if not results:
            continue  # articolo non trovato in questa lingua

        # 2. Altre immagini dalla pagina (se ne servono ancora)
        if len(results) < n:
            try:
                r2 = requests.get(base_mw, params={
                    'action': 'query', 'titles': title,
                    'prop': 'images', 'imlimit': 20, 'format': 'json',
                }, timeout=TIMEOUT, headers={'User-Agent': _UA})
                pages = r2.json().get('query', {}).get('pages', {}).values()
                img_titles = []
                for p in pages:
                    for im in p.get('images', []):
                        t = im.get('title', '')
                        # salta icone, loghi, svg generici
                        lo = t.lower()
                        if any(x in lo for x in ('icon', 'logo', 'flag', 'blank',
                                                   'commons-logo', 'wikidata',
                                                   'question_mark', 'edit-clear')):
                            continue
                        img_titles.append(t)

                # Prendi URL di queste immagini via imageinfo
                needed = n - len(results)
                for chunk_start in range(0, min(len(img_titles), needed * 4), 5):
                    chunk = img_titles[chunk_start:chunk_start + 5]
                    if not chunk:
                        break
                    try:
                        r3 = requests.get(base_mw, params={
                            'action': 'query',
                            'titles': '|'.join(chunk),
                            'prop': 'imageinfo',
                            'iiprop': 'url|mime|size',
                            'iiurlwidth': 500,
                            'format': 'json',
                        }, timeout=TIMEOUT, headers={'User-Agent': _UA})
                        for p in r3.json().get('query', {}).get('pages', {}).values():
                            ii = p.get('imageinfo', [{}])[0]
                            url  = ii.get('thumburl') or ii.get('url', '')
                            mime = ii.get('mime', '')
                            size = ii.get('size', 0)
                            if (url and size > 5000           # salta file piccoli
                                    and 'svg' not in mime     # salta SVG
                                    and url not in {i['url'] for i in results}):
                                results.append({'url': url,
                                                'title': p.get('title','').replace('File:',''),
                                                'src': f'Wikipedia ({lang})'})
                            if len(results) >= n:
                                break
                    except Exception:
                        pass
                    if len(results) >= n:
                        break
            except Exception:
                pass

        return results[:n]

    return []

# ── Download ──────────────────────────────────────────────────────────────────

def _fname_slug(text):
    return re.sub(r'[^\w]+', '_', text.lower().strip())[:30].strip('_')

def download_images(images, output_dir, slug):
    """Scarica in output_dir/img/<slug>__<titolo>_N.ext (nome searchable)."""
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
            if r.status_code == 200 and len(r.content) > 2000:
                with open(local, 'wb') as f:
                    f.write(r.content)
                result.append({**img, 'rel_path': f'img/{fname}', 'local_path': local})
        except Exception:
            pass
    return result

def fetch_for_report(query, output_dir, slug, n=IMG_MAX):
    """All-in-one: cerca + scarica. Ritorna [] se errore."""
    try:
        imgs = _wiki_images(query, n)
        if not imgs:
            return []
        return download_images(imgs, output_dir, slug)
    except Exception:
        return []
