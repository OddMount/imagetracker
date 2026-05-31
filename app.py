from flask import Flask, send_file, request, jsonify, Response
import json, os, urllib.request, urllib.parse, html, re
import requests as req

app = Flask(__name__)

SERPER_KEY = os.environ.get('SERPER_API_KEY', '')

EDITORIAL_DOMAINS = ['hypebeast.com', 'highsnobiety.com', 'vogue.com',
                     'harpersbazaar.com', 'elle.com', 'gq.com', 'wmagazine.com']


# ── 이미지 검색 ──────────────────────────────────────────

def classify_source(url):
    if 'instagram.com' in url: return 'instagram'
    if 'pinterest.com' in url: return 'pinterest'
    if any(d in url for d in EDITORIAL_DOMAINS): return 'editorial'
    return 'general'


def build_attribution(page_url, source_name=''):
    if 'instagram.com' in page_url:
        parts = page_url.rstrip('/').split('/')
        try:
            idx = next(i for i, p in enumerate(parts) if 'instagram.com' in p)
            acct = parts[idx + 1] if idx + 1 < len(parts) else ''
            if acct and acct not in ('p', 'reel', 'tv', 'stories', ''):
                return f"사진/ @{acct} (Instagram)"
        except StopIteration:
            pass
        return "사진/ Instagram"
    if 'pinterest.com' in page_url:
        return "사진/ Pinterest"
    domain = source_name or (page_url.split('/')[2] if page_url.startswith('http') else '')
    return f"사진/ {domain} 제공" if domain else "사진/ 출처 확인 필요"


def serper_images(query, num=10):
    if not SERPER_KEY:
        return []
    try:
        r = req.post(
            'https://google.serper.dev/images',
            headers={'X-API-KEY': SERPER_KEY, 'Content-Type': 'application/json'},
            json={'q': query, 'num': num, 'gl': 'kr', 'hl': 'ko'},
            timeout=10
        )
        return r.json().get('images', [])
    except Exception as e:
        app.logger.error(f"Serper error: {e}")
        return []


def format_serper(items, source_override=None):
    out = []
    for item in items:
        page = item.get('link', '')
        img = item.get('imageUrl', '')
        if not img:
            continue
        st = source_override or classify_source(page)
        out.append({
            'image_url': img,
            'thumbnail': item.get('thumbnailUrl', img),
            'source_url': page,
            'title': item.get('title', ''),
            'source_type': st,
            'attribution': build_attribution(page, item.get('source', '')),
        })
    return out


def search_images(query, brands, extra_filter, instagram, official, editorial, pinterest):
    if not SERPER_KEY:
        return []

    results, seen = [], set()

    def add(items):
        for item in items:
            key = item['image_url']
            if key not in seen:
                seen.add(key)
                results.append(item)

    brand_str = ' '.join(b.lstrip('@') for b in brands)
    f = extra_filter or ''

    if instagram:
        add(format_serper(serper_images(f"site:instagram.com {query} {brand_str} {f}".strip(), 15), 'instagram'))
        for b in brands[:2]:
            handle = b.lstrip('@')
            add(format_serper(serper_images(f"site:instagram.com/{handle} {query}", 8), 'instagram'))

    if editorial:
        sites = ' OR '.join(f"site:{d}" for d in EDITORIAL_DOMAINS[:4])
        add(format_serper(serper_images(f"({sites}) {query} {brand_str} {f}".strip(), 10), 'editorial'))

    if official and brands:
        for b in brands[:2]:
            name = b.lstrip('@')
            add(format_serper(serper_images(
                f"{name} {query} {f} -site:instagram.com -site:pinterest.com".strip(), 8
            ), 'official'))

    if pinterest:
        add(format_serper(serper_images(f"site:pinterest.com {query} {brand_str} {f}".strip(), 8), 'pinterest'))

    if len(results) < 6:
        add(format_serper(serper_images(f"{query} {brand_str} {f}".strip(), 10)))

    return results


# ── 맛집 검색 ────────────────────────────────────────────

def clean_html(text):
    return html.unescape(re.sub('<[^>]+>', '', text or '')).strip()


def search_naver(name, client_id, client_secret):
    query = urllib.parse.quote(name)
    url = f"https://openapi.naver.com/v1/search/local.json?query={query}&display=3&sort=comment"
    r = urllib.request.Request(url)
    r.add_header('X-Naver-Client-Id', client_id)
    r.add_header('X-Naver-Client-Secret', client_secret)
    with urllib.request.urlopen(r, timeout=8) as resp:
        data = json.loads(resp.read().decode())
    return [{
        'name': clean_html(item.get('title', '')),
        'category': item.get('category', ''),
        'road_address': item.get('roadAddress', ''),
        'address': item.get('address', ''),
        'tel': item.get('telephone', ''),
        'description': item.get('description', ''),
        'link': item.get('link', ''),
        'thumbnail': item.get('thumbnail', ''),
        'photos': [],
    } for item in data.get('items', [])]


def search_restaurant_serper(name):
    if not SERPER_KEY:
        return []
    try:
        r = req.post(
            'https://google.serper.dev/search',
            headers={'X-API-KEY': SERPER_KEY, 'Content-Type': 'application/json'},
            json={'q': f"{name} 맛집 주소 전화번호 메뉴", 'gl': 'kr', 'hl': 'ko', 'num': 3},
            timeout=10
        )
        data = r.json()
        results = []
        for item in data.get('organic', []):
            results.append({
                'name': name,
                'category': '',
                'road_address': item.get('snippet', '')[:120],
                'address': '',
                'tel': '',
                'description': item.get('snippet', ''),
                'link': item.get('link', ''),
                'thumbnail': '',
                'photos': [],
                'note': '⚠️ 네이버 API 키 없이 구글 검색 결과',
            })
        return results
    except Exception:
        return []


# ── Flask 라우트 ─────────────────────────────────────────

@app.route('/')
def index():
    return send_file('index.html')


@app.route('/api/image_search', methods=['POST', 'OPTIONS'])
def image_search_route():
    if request.method == 'OPTIONS':
        return _cors()
    body = request.get_json(force=True) or {}
    if not SERPER_KEY:
        return jsonify({'error': 'SERPER_API_KEY not set', 'results': [], 'setup_needed': True}), 200
    try:
        results = search_images(
            query=body.get('query', ''),
            brands=body.get('brands', []),
            extra_filter=body.get('filter', ''),
            instagram=body.get('instagram', True),
            official=body.get('official', True),
            editorial=body.get('editorial', True),
            pinterest=body.get('pinterest', False),
        )
        return jsonify({'results': results})
    except Exception as e:
        return jsonify({'error': str(e), 'results': []}), 200


@app.route('/api/restaurant', methods=['POST', 'OPTIONS'])
def restaurant_route():
    if request.method == 'OPTIONS':
        return _cors()
    body = request.get_json(force=True) or {}
    names = body.get('names', [])
    client_id = body.get('client_id', '').strip()
    client_secret = body.get('client_secret', '').strip()
    all_results = []
    for name in names[:5]:
        try:
            if client_id and client_secret:
                all_results.extend(search_naver(name, client_id, client_secret))
            else:
                all_results.extend(search_restaurant_serper(name))
        except Exception as e:
            all_results.append({'name': name, 'error': str(e)})
    return jsonify({'results': all_results})


@app.route('/api/debug')
def debug():
    return jsonify({
        'serper_key_set': bool(SERPER_KEY),
        'serper_key_prefix': SERPER_KEY[:6] + '...' if SERPER_KEY else None,
    })


def _cors():
    r = Response('', status=200)
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return r


if __name__ == '__main__':
    app.run(debug=True, port=3000)
