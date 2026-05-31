from http.server import BaseHTTPRequestHandler
import json
from duckduckgo_search import DDGS


def build_attribution(result):
    url = result.get('url', '')
    source = result.get('source', '')
    if 'instagram.com' in url:
        parts = url.split('/')
        account = ''
        for i, p in enumerate(parts):
            if p == 'instagram.com' and i + 1 < len(parts):
                account = '@' + parts[i + 1]
                break
        return f"사진/ {account} (Instagram)" if account else "사진/ Instagram"
    if 'pinterest.com' in url:
        return f"사진/ Pinterest ({url})"
    return f"사진/ {source} 제공"


def classify_source(url, source):
    if 'instagram.com' in url:
        return 'instagram'
    if 'pinterest.com' in url:
        return 'pinterest'
    editorial_domains = ['hypebeast.com', 'highsnobiety.com', 'vogue.com', 'gq.com',
                         'harpersbazaar.com', 'elle.com', 'wmagazine.com', 'ssense.com',
                         'farfetch.com', 'matchesfashion.com', 'mrporter.com']
    if any(d in url for d in editorial_domains):
        return 'editorial'
    official_keywords = ['official', 'shop', 'store', 'us.', 'www.']
    if any(k in url for k in official_keywords):
        return 'official'
    return 'general'


def search_images(query, brands, extra_filter, instagram, official, editorial, pinterest):
    results = []
    seen_urls = set()

    def add(items, source_override=None):
        for item in items:
            img_url = item.get('image', '')
            page_url = item.get('url', '')
            if not img_url or img_url in seen_urls:
                continue
            seen_urls.add(img_url)
            source_type = source_override or classify_source(page_url, item.get('source', ''))
            results.append({
                'image_url': img_url,
                'thumbnail': item.get('thumbnail', img_url),
                'source_url': page_url,
                'title': item.get('title', ''),
                'source_type': source_type,
                'attribution': build_attribution({'url': page_url, 'source': item.get('source', '')})
            })

    with DDGS() as ddgs:
        # 1. 인스타그램 공개포스트
        if instagram:
            brand_terms = ' OR '.join(brands) if brands else ''
            ig_q = f"site:instagram.com {query} {brand_terms} {extra_filter}".strip()
            try:
                add(list(ddgs.images(ig_q, max_results=12, safesearch='off')), 'instagram')
            except Exception:
                pass

        # 2. 브랜드 계정 인스타 직접 검색
        if instagram and brands:
            for brand in brands[:3]:
                handle = brand.lstrip('@')
                try:
                    add(list(ddgs.images(
                        f"site:instagram.com/{handle}", max_results=5, safesearch='off'
                    )), 'instagram')
                except Exception:
                    pass

        # 3. 에디터리얼 미디어
        if editorial:
            ed_sites = 'site:hypebeast.com OR site:highsnobiety.com OR site:vogue.com OR site:harpersbazaar.com OR site:elle.com'
            ed_q = f"({ed_sites}) {query} {' '.join(brands)} {extra_filter}".strip()
            try:
                add(list(ddgs.images(ed_q, max_results=10, safesearch='off')), 'editorial')
            except Exception:
                pass

        # 4. 브랜드 공식 사이트
        if official and brands:
            for brand in brands[:3]:
                b = brand.lstrip('@')
                try:
                    add(list(ddgs.images(
                        f"{b} official {query} {extra_filter} -site:instagram.com -site:pinterest.com",
                        max_results=6, safesearch='off'
                    )), 'official')
                except Exception:
                    pass

        # 5. 핀터레스트
        if pinterest:
            pin_q = f"site:pinterest.com {query} {' '.join(brands)} {extra_filter}".strip()
            try:
                add(list(ddgs.images(pin_q, max_results=8, safesearch='off')), 'pinterest')
            except Exception:
                pass

        # 6. 일반 보완 검색 (결과 부족 시)
        if len(results) < 10:
            try:
                add(list(ddgs.images(
                    f"{query} {' '.join(brands)} {extra_filter}".strip(),
                    max_results=10, safesearch='off'
                )), 'general')
            except Exception:
                pass

    return results


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            results = search_images(
                query=body.get('query', ''),
                brands=body.get('brands', []),
                extra_filter=body.get('filter', ''),
                instagram=body.get('instagram', True),
                official=body.get('official', True),
                editorial=body.get('editorial', True),
                pinterest=body.get('pinterest', False),
            )
            self._json(200, {'results': results})
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
