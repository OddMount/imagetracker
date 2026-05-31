from flask import Flask, send_file, request, jsonify
import json
import urllib.request
import urllib.parse
import html
import re
from duckduckgo_search import DDGS

app = Flask(__name__)


# ── 이미지 검색 유틸 ─────────────────────────────────────

def build_attribution(page_url, source_name=''):
    if 'instagram.com' in page_url:
        parts = page_url.rstrip('/').split('/')
        try:
            ig_idx = next(i for i, p in enumerate(parts) if 'instagram.com' in p)
            account = parts[ig_idx + 1] if ig_idx + 1 < len(parts) else ''
            if account and account not in ('p', 'reel', 'tv', 'stories'):
                return f"사진/ @{account} (Instagram)"
        except StopIteration:
            pass
        return "사진/ Instagram"
    if 'pinterest.com' in page_url:
        return "사진/ Pinterest"
    return f"사진/ {source_name} 제공" if source_name else "사진/ 출처 확인 필요"


def classify_source(url):
    if 'instagram.com' in url:
        return 'instagram'
    if 'pinterest.com' in url:
        return 'pinterest'
    if any(d in url for d in ['hypebeast.com', 'highsnobiety.com', 'vogue.com',
                               'harpersbazaar.com', 'elle.com', 'gq.com', 'wmagazine.com']):
        return 'editorial'
    return 'general'


def ddg_images(query, max_results=10):
    try:
        with DDGS() as ddgs:
            return list(ddgs.images(query, max_results=max_results, safesearch='off'))
    except Exception as e:
        app.logger.error(f"DDG error for '{query}': {type(e).__name__}: {e}")
        return []


@app.route('/api/debug')
def debug():
    errors = []
    results = []
    try:
        with DDGS() as ddgs:
            hits = list(ddgs.images('rain jacket', max_results=3, safesearch='off'))
            results = [h.get('url', '') for h in hits]
    except Exception as e:
        errors.append(f"{type(e).__name__}: {str(e)}")
    return jsonify({'ddg_results': results, 'errors': errors, 'python': __import__('sys').version})


def search_images(query, brands, extra_filter, instagram, official, editorial, pinterest):
    results = []
    seen = set()

    def add(items, source_type_override=None):
        for item in items:
            img = item.get('image', '')
            page = item.get('url', '')
            if not img or img in seen:
                continue
            seen.add(img)
            st = source_type_override or classify_source(page)
            results.append({
                'image_url': img,
                'thumbnail': item.get('thumbnail', img),
                'source_url': page,
                'title': item.get('title', ''),
                'source_type': st,
                'attribution': build_attribution(page, item.get('source', '')),
            })

    brand_str = ' '.join(brands)
    filter_str = extra_filter or ''

    if instagram:
        add(ddg_images(f"site:instagram.com {query} {brand_str} {filter_str}".strip(), 12), 'instagram')
        for b in brands[:3]:
            handle = b.lstrip('@')
            add(ddg_images(f"site:instagram.com/{handle} {query}", 5), 'instagram')

    if editorial:
        sites = 'site:hypebeast.com OR site:highsnobiety.com OR site:vogue.com OR site:harpersbazaar.com OR site:elle.com'
        add(ddg_images(f"({sites}) {query} {brand_str} {filter_str}".strip(), 10), 'editorial')

    if official and brands:
        for b in brands[:3]:
            name = b.lstrip('@')
            add(ddg_images(
                f"{name} official {query} {filter_str} -site:instagram.com -site:pinterest.com".strip(), 6
            ), 'official')

    if pinterest:
        add(ddg_images(f"site:pinterest.com {query} {brand_str} {filter_str}".strip(), 8), 'pinterest')

    if len(results) < 8:
        add(ddg_images(f"{query} {brand_str} {filter_str}".strip(), 10))

    return results


# ── 맛집 검색 유틸 ───────────────────────────────────────

def clean_html(text):
    return html.unescape(re.sub('<[^>]+>', '', text or '')).strip()


def search_naver(name, client_id, client_secret):
    query = urllib.parse.quote(name)
    url = f"https://openapi.naver.com/v1/search/local.json?query={query}&display=3&sort=comment"
    req = urllib.request.Request(url)
    req.add_header('X-Naver-Client-Id', client_id)
    req.add_header('X-Naver-Client-Secret', client_secret)
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read().decode())
    return [
        {
            'name': clean_html(item.get('title', '')),
            'category': item.get('category', ''),
            'road_address': item.get('roadAddress', ''),
            'address': item.get('address', ''),
            'tel': item.get('telephone', ''),
            'description': item.get('description', ''),
            'link': item.get('link', ''),
            'thumbnail': item.get('thumbnail', ''),
            'photos': [],
        }
        for item in data.get('items', [])
    ]


def search_restaurant_fallback(name):
    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(f"site:map.naver.com {name}", max_results=3))
        return [
            {
                'name': name,
                'category': '',
                'road_address': h.get('body', '')[:120],
                'address': '',
                'tel': '',
                'description': h.get('body', ''),
                'link': h.get('href', ''),
                'thumbnail': '',
                'photos': [],
                'note': '⚠️ 네이버 API 키 없이 검색 — 정확도가 낮을 수 있어요',
            }
            for h in hits
        ]
    except Exception:
        return []


# ── Flask 라우트 ─────────────────────────────────────────

@app.route('/')
def index():
    return send_file('index.html')


@app.route('/api/image_search', methods=['POST', 'OPTIONS'])
def image_search_route():
    if request.method == 'OPTIONS':
        return _cors_ok()
    body = request.get_json(force=True) or {}
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
        return jsonify({'error': str(e)}), 500


@app.route('/api/restaurant', methods=['POST', 'OPTIONS'])
def restaurant_route():
    if request.method == 'OPTIONS':
        return _cors_ok()
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
                all_results.extend(search_restaurant_fallback(name))
        except Exception as e:
            all_results.append({'name': name, 'error': str(e)})
    return jsonify({'results': all_results})


def _cors_ok():
    from flask import Response
    r = Response('', status=200)
    r.headers['Access-Control-Allow-Origin'] = '*'
    r.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return r


if __name__ == '__main__':
    app.run(debug=True, port=3000)
