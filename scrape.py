"""
하퍼스 바자 이미지트래커 — 범용 스크래퍼
사용법:
  python3 scrape.py {slug}          # instagram + naver_place 자동 수집
  python3 scrape.py {slug} --login  # 인스타 세션 재로그인
"""

import sys
import os
import json
import re
import io
import time
import random
import getpass
import urllib.parse
import requests
import instaloader
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

# ── 설정 ─────────────────────────────────────────────────────────

SESSION_DIR = Path.home() / ".config" / "instaloader"
MAX_IG      = 20    # 인스타 최대 수집
MAX_NAVER   = 12    # 네이버 최대 수집
MIN_SIZE    = 100_000  # 네이버 썸네일 제외 기준 (100KB)

MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
IG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
    "X-IG-App-ID": "936619743392459",
}


# ── Config 로드 ───────────────────────────────────────────────────

def load_config(slug):
    path = Path(f"articles/{slug}.json")
    if not path.exists():
        print(f"오류: articles/{slug}.json 없음")
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


# ── 인스타그램 ────────────────────────────────────────────────────

def ig_login(force=False):
    sessions = list(SESSION_DIR.glob("session-*"))
    if sessions and not force:
        L = instaloader.Instaloader(quiet=True)
        uname = sessions[0].name.replace("session-", "")
        L.load_session_from_file(uname, str(sessions[0]))
        print(f"인스타 세션 로드: @{uname}")
        return L.context._session
    print("Instagram 로그인")
    username = input("아이디: ").strip()
    password = getpass.getpass("비밀번호: ")
    L = instaloader.Instaloader(quiet=True)
    try:
        L.login(username, password)
    except instaloader.exceptions.TwoFactorAuthRequiredException:
        L.two_factor_login(input("2FA 코드: ").strip())
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    L.save_session_to_file(str(SESSION_DIR / f"session-{username}"))
    return L.context._session


def ig_fetch_profile(session, account, max_posts=100):
    """프로필 + 포스트 가져오기. 첫 페이지 후 instaloader로 페이지네이션."""
    r = session.get(
        f"https://www.instagram.com/api/v1/users/web_profile_info/?username={account}",
        headers=IG_HEADERS, timeout=15
    )
    if r.status_code != 200:
        print(f"  프로필 API 실패: {r.status_code}")
        return None, {}
    data = r.json()
    if "data" not in data or not data["data"].get("user"):
        return None, {}
    user = data["data"]["user"]
    meta = {
        "full_name": user.get("full_name", ""),
        "biography": user.get("biography", ""),
        "is_private": user.get("is_private", False),
    }

    def parse_edges(edges):
        out = {}
        for edge in edges:
            node = edge["node"]
            sc = node["shortcode"]
            if node.get("__typename") == "GraphSidecar":
                children = node.get("edge_sidecar_to_children", {}).get("edges", [])
                img_url = children[0]["node"]["display_url"] if children else node["display_url"]
            else:
                img_url = node["display_url"]
            cap_edges = node.get("edge_media_to_caption", {}).get("edges", [])
            out[sc] = {
                "img_url": img_url,
                "caption": cap_edges[0]["node"]["text"] if cap_edges else "",
            }
        return out

    media = user.get("edge_owner_to_timeline_media", {})
    posts = parse_edges(media.get("edges", []))
    page_info = media.get("page_info", {})
    user_id = user.get("id", "")

    # 페이지네이션: max_posts에 도달하거나 다음 페이지 없을 때까지
    while len(posts) < max_posts and page_info.get("has_next_page") and user_id:
        cursor = page_info.get("end_cursor", "")
        if not cursor:
            break
        try:
            time.sleep(random.uniform(2, 4))
            r2 = session.get(
                "https://www.instagram.com/graphql/query/",
                params={
                    "query_hash": "e769aa130647d2354c40ea6a439bfc08",
                    "variables": json.dumps({"id": user_id, "first": 12, "after": cursor}),
                },
                headers=IG_HEADERS, timeout=15
            )
            if r2.status_code != 200:
                break
            page_data = r2.json()
            media2 = page_data.get("data", {}).get("user", {}).get("edge_owner_to_timeline_media", {})
            new_edges = media2.get("edges", [])
            if not new_edges:
                break
            posts.update(parse_edges(new_edges))
            page_info = media2.get("page_info", {})
        except Exception:
            break

    print(f"  총 {len(posts)}개 포스트 수집")
    return meta, posts


def ig_fetch_posts(session, account, max_posts=50):
    _, posts = ig_fetch_profile(session, account, max_posts)
    return posts


def verify_account(meta, spot):
    """bio에 addr 키워드가 하나라도 포함되면 통과. 없으면 경고."""
    if not meta:
        return
    addr = spot.get("addr", "")
    bio = (meta.get("biography", "") + " " + meta.get("full_name", "")).lower()
    # 주소에서 의미있는 단어 추출 (구/동/로 단위)
    import re
    addr_words = re.findall(r'[가-힣]{2,}', addr)
    # 서울, 구, 동, 로 이름 중 bio에 있으면 OK
    matches = [w for w in addr_words if len(w) >= 3 and w in bio]
    name = spot.get("name", "")
    acct = spot.get("account", "")
    if matches:
        print(f"  ✅ bio 검증 통과: {matches[0]} 포함됨")
    else:
        print(f"  ⚠️  bio에 주소 키워드 없음 — 계정이 맞는지 확인 필요")
        print(f"     계정: @{acct}  |  이름: {meta.get('full_name')}  |  bio: {meta.get('biography', '')[:60]}")


def scrape_instagram(spot, slug, ig_session):
    account  = spot["account"]
    dir_name = spot["dir"]
    keywords = spot.get("keywords", "").split()
    dest = Path(f"references/images/{slug}/{dir_name}")
    dest.mkdir(parents=True, exist_ok=True)
    for f in dest.glob("*.jpg"):
        f.unlink()

    print(f"  @{account} 스크래핑...")
    try:
        meta, feed = ig_fetch_profile(ig_session, account)
        verify_account(meta, spot)
        if not feed:
            return []
        scored = []
        for sc, info in feed.items():
            caption = info["caption"].lower()
            score = sum(1 for kw in keywords if kw in caption)
            scored.append((score, sc, info["img_url"]))
        scored.sort(key=lambda x: -x[0])

        saved = []
        dl_headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.instagram.com/"}
        for score, sc, img_url in scored[:MAX_IG]:
            filepath = dest / f"{sc}.jpg"
            try:
                r = requests.get(img_url, headers=dl_headers, timeout=20)
                r.raise_for_status()
                filepath.write_bytes(r.content)
                tag = f"관련({score}점)" if score > 0 else "최신"
                print(f"    [{tag}] {sc}")
                saved.append({"path": str(filepath), "post_id": sc,
                              "post_url": f"https://www.instagram.com/p/{sc}/"})
                time.sleep(random.uniform(0.8, 1.5))  # 이미지 다운로드 사이
            except Exception as e:
                print(f"    다운로드 실패 {sc}: {e}")
        return saved
    except Exception as e:
        print(f"  실패: {e}")
        return []


# ── 네이버 플레이스 ───────────────────────────────────────────────

def get_place_id(query):
    headers = {"User-Agent": MOBILE_UA, "Accept-Language": "ko-KR,ko;q=0.9"}
    try:
        r = requests.get(
            "https://m.search.naver.com/search.naver",
            params={"query": query, "where": "m"},
            headers=headers, timeout=10
        )
        pattern = r'place\.naver\.com/(?:restaurant|cafe|place)/(\d+)[^"]*"[^>]*>([^<]{1,40})'
        matches = re.findall(pattern, r.text)
        if matches:
            pid, name = matches[0]
            print(f"    place_id: {pid} ({name.strip()})")
            return pid
    except Exception as e:
        print(f"    place_id 검색 실패: {e}")
    return None


def scrape_naver(spot, slug):
    query    = spot.get("naver_query", spot["name"])
    dir_name = spot["dir"]
    dest = Path(f"references/images/{slug}/{dir_name}/naver")
    dest.mkdir(parents=True, exist_ok=True)
    for f in dest.glob("naver_*.jpg"):
        f.unlink()

    print(f"  네이버 플레이스: {spot['name']}")
    place_id = spot.get("naver_place_id") or get_place_id(query)
    if not place_id:
        return []

    business_urls = []

    def on_response(response):
        url = response.url
        if "ldb-phinf" in url and url not in business_urls:
            business_urls.append(url)
        if "search.pstatic.net" in url and "src=" in url:
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            src = params.get("src", [""])[0]
            if "ldb-phinf" in src and src not in business_urls:
                business_urls.append(src)

    tab_suffixes = ["photo/business", "photo/menu"]
    place_types  = ["restaurant", "cafe", "place"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=MOBILE_UA,
            viewport={"width": 390, "height": 844},
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
        )
        page.on("response", on_response)

        success = False
        used_url = ""
        for tab in tab_suffixes:
            for ptype in place_types:
                url = f"https://m.place.naver.com/{ptype}/{place_id}/{tab}"
                try:
                    page.goto(url, wait_until="load", timeout=20000)
                    page.wait_for_timeout(1500)
                    title = page.title()
                    if "찾을 수 없" not in title and "없는 페이지" not in title:
                        if not used_url:
                            used_url = url
                        success = True
                        for _ in range(8):
                            page.evaluate("window.scrollBy(0, 600)")
                            page.wait_for_timeout(500)
                        break
                except Exception:
                    continue

        browser.close()

    if not success:
        return []

    print(f"    업체제공 URL {len(business_urls)}개 수집")
    dl_headers = {"User-Agent": MOBILE_UA, "Referer": "https://m.place.naver.com/"}
    saved = []
    idx = 0
    for src in business_urls:
        if len(saved) >= MAX_NAVER:
            break
        try:
            r = requests.get(src, headers=dl_headers, timeout=15)
            r.raise_for_status()
            if len(r.content) < MIN_SIZE:
                continue
            fname = dest / f"naver_{idx:02d}.jpg"
            fname.write_bytes(r.content)
            saved.append({"path": str(fname), "src_url": src, "naver_url": used_url, "dir": dir_name})
            print(f"    저장: {fname.name} ({len(r.content)//1024}KB)")
            idx += 1
        except Exception as e:
            print(f"    다운로드 실패: {e}")

    return saved


# ── Google 이미지 검색 (Playwright) ──────────────────────────────

SKIP_DOMAINS = ["pinimg.com", "pinterest.com", "bing.com"]

def scrape_google_images(spot, slug):
    query    = spot.get("query", spot["name"])
    dir_name = spot["dir"]
    max_imgs = spot.get("max", 12)
    dest = Path(f"references/images/{slug}/{dir_name}")
    dest.mkdir(parents=True, exist_ok=True)
    for f in dest.glob("gimg_*.jpg"):
        f.unlink()

    print(f"  Bing 이미지: {query}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        try:
            page.goto(
                f"https://www.bing.com/images/search?q={urllib.parse.quote(query)}&first=0",
                wait_until="networkidle",
                timeout=30000,
            )
            page.wait_for_timeout(2000)
            for _ in range(3):
                page.evaluate("window.scrollBy(0, 900)")
                page.wait_for_timeout(600)
            content = page.content()
        except Exception as e:
            print(f"  페이지 로드 실패: {e}")
            content = ""
        browser.close()

    # Bing: mediaurl=<URL encoded> 패턴에서 원본 이미지 URL 추출
    raw = re.findall(r'mediaurl=([^&"\'>\s]+)', content)
    seen = set()
    image_urls = []
    for r in raw:
        decoded = urllib.parse.unquote(r)
        if decoded.startswith("http") and decoded not in seen:
            if not any(d in decoded for d in SKIP_DOMAINS):
                seen.add(decoded)
                image_urls.append(decoded)

    print(f"    후보 {len(image_urls)}개")

    saved = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    idx = 0
    for url in image_urls:
        if idx >= max_imgs:
            break
        try:
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            if len(r.content) < 15_000:
                continue
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            fname = dest / f"gimg_{idx:02d}.jpg"
            img.save(fname, "JPEG", quality=92)
            saved.append({"path": str(fname), "src_url": url, "query": query})
            print(f"    [{idx}] {fname.name}  {img.size[0]}x{img.size[1]}  {len(r.content)//1024}KB")
            idx += 1
        except Exception as e:
            print(f"    스킵: {e}")

    return saved


# ── manual (URL 직접 다운로드) ────────────────────────────────────

def scrape_manual(spot, slug):
    urls = spot.get("urls", [])
    if not urls:
        print(f"  {spot['name']}: manual URLs 없음, 스킵")
        return []

    dir_name = spot["dir"]
    dest = Path(f"references/images/{slug}/{dir_name}")
    dest.mkdir(parents=True, exist_ok=True)

    saved = []
    for i, url in enumerate(urls):
        ext = url.split("?")[0].rsplit(".", 1)[-1] or "jpg"
        fname = dest / f"manual_{i:02d}.{ext}"
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            fname.write_bytes(r.content)
            saved.append({"path": str(fname), "src_url": url})
            print(f"    저장: {fname.name}")
        except Exception as e:
            print(f"    다운로드 실패 [{i}]: {e}")
    return saved


# ── 카카오맵 ─────────────────────────────────────────────────────

KAKAO_KEY = "267d778c8ded2bf38f5adf46b62c0798"  # Jarvis 앱 REST API 키

def get_kakao_key():
    return os.environ.get("KAKAO_REST_KEY", KAKAO_KEY)

def get_kakao_place_id(query):
    key = get_kakao_key()
    if not key:
        return None
    try:
        r = requests.get(
            "https://dapi.kakao.com/v2/local/search/keyword.json",
            params={"query": query, "size": 3},
            headers={"Authorization": f"KakaoAK {key}"},
            timeout=10
        )
        if not r.ok:
            print(f"    카카오 API 실패: {r.status_code} {r.text[:80]}")
            return None
        docs = r.json().get("documents", [])
        if docs:
            print(f"    kakao_id: {docs[0]['id']} ({docs[0]['place_name']})")
            return docs[0]["id"]
    except Exception as e:
        print(f"    카카오 place_id 검색 실패: {e}")
    return None


def scrape_kakao(spot, slug):
    query    = spot.get("naver_query", spot["name"])
    dir_name = spot["dir"]
    dest = Path(f"references/images/{slug}/{dir_name}/kakao")
    dest.mkdir(parents=True, exist_ok=True)
    for f in dest.glob("kakao_*.jpg"):
        f.unlink()

    print(f"  카카오맵: {spot['name']}")
    place_id = spot.get("kakao_place_id") or get_kakao_place_id(query)
    if not place_id and query != spot["name"]:
        print(f"    naver_query로 실패, 업체명만으로 재시도: {spot['name']}")
        place_id = get_kakao_place_id(spot["name"])
    if not place_id:
        print("    place_id 없음, 스킵")
        return []

    # 업체 제공 사진 CDN 패턴 (리뷰/블로그 제외)
    KAKAO_OFFICIAL_CDN = ["fiy_reboot/place/", "mystore/"]
    place_url = f"https://place.map.kakao.com/{place_id}"
    image_urls = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=MOBILE_UA,
            viewport={"width": 390, "height": 844},
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
        )
        try:
            page.goto(place_url, wait_until="networkidle", timeout=25000)
            page.wait_for_timeout(2000)
            for _ in range(15):
                page.evaluate("window.scrollBy(0, 400)")
                page.wait_for_timeout(300)
            # img src에서 fname 파라미터 디코딩 → 업체 제공 CDN만 필터
            srcs = page.eval_on_selector_all(
                "img[src*='img1.kakaocdn.net']",
                "els => els.map(e => e.src)"
            )
            seen = set()
            for src in srcs:
                parsed = urllib.parse.urlparse(src)
                params = urllib.parse.parse_qs(parsed.query)
                fname = params.get("fname", [""])[0]
                if not fname:
                    continue
                decoded = urllib.parse.unquote(fname)
                if any(cdn in decoded for cdn in KAKAO_OFFICIAL_CDN) and decoded not in seen:
                    seen.add(decoded)
                    image_urls.append(decoded)
        except Exception as e:
            print(f"    페이지 로드 실패: {e}")
        browser.close()

    print(f"    업체제공 이미지 {len(image_urls)}개 수집")
    dl_headers = {"User-Agent": MOBILE_UA, "Referer": "https://place.map.kakao.com/"}
    saved = []
    for idx, src in enumerate(image_urls[:MAX_NAVER]):
        try:
            r = requests.get(src, headers=dl_headers, timeout=15)
            r.raise_for_status()
            if len(r.content) < MIN_SIZE:
                continue
            fname = dest / f"kakao_{idx:02d}.jpg"
            fname.write_bytes(r.content)
            saved.append({"path": str(fname), "src_url": src, "kakao_url": place_url, "dir": dir_name})
            print(f"    저장: {fname.name} ({len(r.content)//1024}KB)")
        except Exception as e:
            print(f"    다운로드 실패: {e}")

    return saved


# ── HTML 생성/업데이트 ────────────────────────────────────────────

GITHUB_RAW = "https://raw.githubusercontent.com/OddMount/imagetracker/master/references/images"

def _dl_url(web_path, dl_name):
    """GitHub raw는 크로스오리진 → download 속성 무시. /api/download 프록시로 우회."""
    import urllib.parse as _up
    raw = GITHUB_RAW + web_path.replace("/ref/images", "")
    return f"/api/download?url={_up.quote(raw, safe='')}&filename={_up.quote(dl_name, safe='')}"

def card_ig(item, spot, slug):
    post_id  = item["post_id"]
    dir_name = spot["dir"]
    account  = spot["account"]
    web_path = f"/ref/images/{slug}/{dir_name}/{post_id}.jpg"
    return f"""<div class="card ig-card">
  <div class="img-wrap">
    <img src="{web_path}" loading="lazy" onerror="this.closest('.card').style.display='none'">
    <span class="badge" style="background:#e1306c">Instagram</span>
    <a class="dl-btn" href="{_dl_url(web_path, f'{dir_name}_{post_id}')}">⬇ JPG</a>
  </div>
  <div class="meta">
    <p class="attr">사진/ @{account}</p>
    <a class="src" href="{item['post_url']}" target="_blank">출처 확인 →</a>
  </div>
</div>"""


def card_naver(item, spot, slug):
    dir_name = spot["dir"]
    fname    = Path(item["path"]).name
    web_path = f"/ref/images/{slug}/{dir_name}/naver/{fname}"
    return f"""<div class="card naver-card">
  <div class="img-wrap">
    <img src="{web_path}" loading="lazy" onerror="this.closest('.card').style.display='none'">
    <span class="badge" style="background:#03c75a">네이버</span>
    <a class="dl-btn" href="{_dl_url(web_path, f'{dir_name}_{fname}')}">⬇ JPG</a>
  </div>
  <div class="meta">
    <p class="attr">사진/ 업체제공 (네이버 플레이스)</p>
    <a class="src" href="{item['naver_url']}" target="_blank">출처 확인 →</a>
  </div>
</div>"""


def card_google(item, spot, slug):
    dir_name = spot["dir"]
    fname    = Path(item["path"]).name
    web_path = f"/ref/images/{slug}/{dir_name}/{fname}"
    src_url  = item["src_url"]
    domain   = src_url.split("/")[2] if src_url.startswith("http") else ""
    return f"""<div class="card google-card">
  <div class="img-wrap">
    <img src="{web_path}" loading="lazy" onerror="this.closest('.card').style.display='none'">
    <span class="badge" style="background:#4285f4">Google</span>
    <a class="dl-btn" href="{web_path}" download="{dir_name}_{fname}">⬇ JPG</a>
  </div>
  <div class="meta">
    <p class="attr">출처/ {domain}</p>
    <a class="src" href="{src_url}" target="_blank">원본 확인 →</a>
  </div>
</div>"""


def card_kakao(item, spot, slug):
    dir_name = spot["dir"]
    fname    = Path(item["path"]).name
    web_path = f"/ref/images/{slug}/{dir_name}/kakao/{fname}"
    return f"""<div class="card kakao-card">
  <div class="img-wrap">
    <img src="{web_path}" loading="lazy" onerror="this.closest('.card').style.display='none'">
    <span class="badge" style="background:#ffcd00;color:#3c1e1e">카카오</span>
    <a class="dl-btn" href="{_dl_url(web_path, f'{dir_name}_{fname}')}">⬇ JPG</a>
  </div>
  <div class="meta">
    <p class="attr">사진/ 업체제공 (카카오맵)</p>
    <a class="src" href="{item['kakao_url']}" target="_blank">출처 확인 →</a>
  </div>
</div>"""


def card_manual(item, spot, slug):
    dir_name = spot["dir"]
    fname    = Path(item["path"]).name
    web_path = f"/ref/images/{slug}/{dir_name}/{fname}"
    return f"""<div class="card manual-card">
  <div class="img-wrap">
    <img src="{web_path}" loading="lazy" onerror="this.closest('.card').style.display='none'">
    <span class="badge" style="background:#888">직접제공</span>
    <a class="dl-btn" href="{web_path}" download="{dir_name}_{fname}">⬇ JPG</a>
  </div>
  <div class="meta">
    <p class="attr">사진/ 업체 제공</p>
    <a class="src" href="{item['src_url']}" target="_blank">출처 확인 →</a>
  </div>
</div>"""


SECTION_TMPL = """<section id="sec-{dir}" style="border-top:3px solid {color};padding-top:24px;margin-bottom:48px">
  <h2 style="margin:0 0 4px;font-size:1.3rem">{name}</h2>
  <p style="margin:0 0 16px;color:#666;font-size:.9rem">{addr}</p>
  <div class="grid">
    <div class="ig-grid ig-{dir}">{ig_cards}</div>
    <div class="ig-grid naver-grid naver-{dir}">{naver_cards}</div>
    <div class="ig-grid kakao-grid kakao-{dir}">{kakao_cards}</div>
    <div class="ig-grid manual-grid manual-{dir}">{manual_cards}</div>
  </div>
</section>"""

HTML_TMPL = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — 이미지 레퍼런스</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,sans-serif;background:#fafafa;color:#111;padding:24px}}
h1{{font-size:1.5rem;margin-bottom:24px}}
.grid{{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}}
.ig-grid{{display:flex;flex-wrap:wrap;gap:12px;width:100%}}
.card{{width:200px;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 6px rgba(0,0,0,.1)}}
.img-wrap{{position:relative;overflow:hidden;background:#f0f0f0;aspect-ratio:1}}
.img-wrap img{{width:100%;height:100%;object-fit:cover;display:block;transition:transform .2s}}
.img-wrap:hover img{{transform:scale(1.04)}}
.badge{{position:absolute;top:8px;left:8px;font-size:10px;color:#fff;padding:2px 6px;border-radius:4px;font-weight:600}}
.dl-btn{{position:absolute;bottom:8px;right:8px;background:rgba(0,0,0,.6);color:#fff;font-size:11px;padding:3px 8px;border-radius:4px;text-decoration:none}}
.meta{{padding:8px 10px}}
.attr{{font-size:11px;color:#444;margin-bottom:4px}}
.src{{font-size:11px;color:#0066cc;text-decoration:none}}
</style>
</head>
<body>
<h1>{title}</h1>
{sections}
</body>
</html>"""


def build_html(config, all_saved, slug):
    html_file = Path(f"references/{slug}.html")
    sections  = []

    for spot in config["spots"]:
        dir_name = spot["dir"]
        saved    = all_saved.get(dir_name, {})

        ig_cards     = "".join(card_ig(i, spot, slug)     for i in saved.get("instagram", []))
        naver_cards  = "".join(card_naver(i, spot, slug)  for i in saved.get("naver", []))
        kakao_cards  = "".join(card_kakao(i, spot, slug)  for i in saved.get("kakao", []))
        google_cards = "".join(card_google(i, spot, slug) for i in saved.get("google", []))
        manual_cards = "".join(card_manual(i, spot, slug) for i in saved.get("manual", []))

        sections.append(SECTION_TMPL.format(
            dir=dir_name,
            color=spot.get("color", "#333"),
            name=spot["name"],
            addr=spot.get("addr", ""),
            ig_cards=ig_cards,
            naver_cards=naver_cards + google_cards,
            kakao_cards=kakao_cards,
            manual_cards=manual_cards,
        ))

    html = HTML_TMPL.format(title=config.get("title", slug), sections="\n".join(sections))
    html_file.write_text(html, encoding="utf-8")
    print(f"HTML 생성: {html_file}")


# ── 메인 ─────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("사용법: python3 scrape.py {slug} [--login]")
        sys.exit(1)

    slug        = sys.argv[1]
    force_login = "--login" in sys.argv
    kakao_only  = "--kakao-only" in sys.argv
    config      = load_config(slug)

    # 인스타 필요 여부 확인 (kakao-only면 스킵)
    needs_ig = not kakao_only and any(
        "instagram" in (s.get("type") if isinstance(s.get("type"), list) else [s.get("type", "")])
        or s.get("type") == "instagram"
        for s in config["spots"]
    )
    ig_session = ig_login(force=force_login) if needs_ig else None

    # kakao-only 모드면 기존 이미지 유지하며 all_saved 초기화
    all_saved = {}
    if kakao_only:
        print("카카오 전용 모드 — 기존 인스타/네이버 이미지 유지")
        for spot in config["spots"]:
            d = spot["dir"]
            all_saved[d] = {"instagram": [], "naver": [], "kakao": [], "google": [], "manual": []}
            for sc in Path(f"references/images/{slug}/{d}").glob("*.jpg"):
                all_saved[d]["instagram"].append({"path": str(sc), "post_id": sc.stem,
                                                   "post_url": f"https://www.instagram.com/p/{sc.stem}/"})
            for nf in Path(f"references/images/{slug}/{d}/naver").glob("naver_*.jpg"):
                all_saved[d]["naver"].append({"path": str(nf), "src_url": "", "naver_url": "", "dir": d})

    for spot in config["spots"]:
        dir_name = spot["dir"]
        types    = spot.get("type", [])
        if isinstance(types, str):
            types = [types]

        print(f"\n{'─'*40}")
        print(f"{spot['name']}")
        if not kakao_only:
            all_saved[dir_name] = {}

        if not kakao_only and "instagram" in types and ig_session:
            result = scrape_instagram(spot, slug, ig_session)
            all_saved[dir_name]["instagram"] = result
            delay = random.uniform(18, 28)
            print(f"  ⏳ 다음 계정까지 {delay:.0f}초 대기...")
            time.sleep(delay)

        if not kakao_only and "naver_place" in types:
            result = scrape_naver(spot, slug)
            all_saved[dir_name]["naver"] = result
            time.sleep(3)

        if "naver_place" in types or "kakao_place" in types:
            result = scrape_kakao(spot, slug)
            all_saved[dir_name]["kakao"] = result
            time.sleep(2)

        if "google_images" in types:
            result = scrape_google_images(spot, slug)
            all_saved[dir_name]["google"] = result
            time.sleep(2)

        if "manual" in types:
            result = scrape_manual(spot, slug)
            all_saved[dir_name]["manual"] = result

    # 결과 요약
    print(f"\n{'='*40}")
    print("수집 결과")
    print(f"{'='*40}")
    for spot in config["spots"]:
        d = all_saved.get(spot["dir"], {})
        ig_n  = len(d.get("instagram", []))
        nav_n = len(d.get("naver", []))
        kak_n = len(d.get("kakao", []))
        goo_n = len(d.get("google", []))
        man_n = len(d.get("manual", []))
        print(f"  {spot['name']}: IG {ig_n}장 / 네이버 {nav_n}장 / 카카오 {kak_n}장 / Google {goo_n}장 / manual {man_n}장")

    build_html(config, all_saved, slug)

    print(f"""
배포:
  git add references/ articles/ && git commit -m "Add {slug} images" && git push

URL: https://imagetracker-nine.vercel.app/ref/{slug}
""")


if __name__ == "__main__":
    main()
