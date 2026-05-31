from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.parse
import html
import re


def clean(text):
    return html.unescape(re.sub('<[^>]+>', '', text or '')).strip()


def search_naver(name, client_id, client_secret):
    query = urllib.parse.quote(name)
    url = f"https://openapi.naver.com/v1/search/local.json?query={query}&display=3&sort=comment"
    req = urllib.request.Request(url)
    req.add_header('X-Naver-Client-Id', client_id)
    req.add_header('X-Naver-Client-Secret', client_secret)
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read().decode())
    items = data.get('items', [])
    results = []
    for item in items:
        results.append({
            'name': clean(item.get('title', '')),
            'category': item.get('category', ''),
            'road_address': item.get('roadAddress', ''),
            'address': item.get('address', ''),
            'tel': item.get('telephone', ''),
            'description': item.get('description', ''),
            'link': item.get('link', ''),
            'thumbnail': item.get('thumbnail', ''),
            'photos': [],
        })
    return results


def search_naver_ddg_fallback(name):
    """API 키 없을 때 DuckDuckGo로 네이버 플레이스 정보 스크랩"""
    from duckduckgo_search import DDGS
    results = []
    with DDGS() as ddgs:
        hits = list(ddgs.text(
            f"site:map.naver.com {name} 맛집 주소 전화",
            max_results=3
        ))
        for h in hits:
            results.append({
                'name': name,
                'category': '',
                'road_address': h.get('body', '')[:100],
                'address': '',
                'tel': '',
                'description': h.get('body', ''),
                'link': h.get('href', ''),
                'thumbnail': '',
                'photos': [],
                'note': '⚠️ API 키 없이 검색한 결과 — 정확도가 낮을 수 있어요'
            })
    return results


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            names = body.get('names', [])
            client_id = body.get('client_id', '').strip()
            client_secret = body.get('client_secret', '').strip()

            all_results = []
            for name in names[:5]:
                if client_id and client_secret:
                    try:
                        items = search_naver(name, client_id, client_secret)
                        all_results.extend(items)
                    except Exception as e:
                        all_results.append({'name': name, 'error': str(e)})
                else:
                    items = search_naver_ddg_fallback(name)
                    all_results.extend(items)

            self._json(200, {'results': all_results})
        except Exception as e:
            self._json(500, {'error': str(e)})

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def log_message(self, *args):
        pass
