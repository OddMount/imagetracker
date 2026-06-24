"""
하퍼스 바자 이미지트래커 — 범용 스크래퍼
사용법:
  python3 scrape.py {slug}          # instagram + naver_place 자동 수집
  python3 scrape.py {slug} --login  # 인스타 세션 재로그인
"""

import sys
import json
import re
import io
import time
import getpass
import urllib.parse
import requests
import instaloader
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright

# ── 설정 ─────────────────────────────────────────────────────────

SESSION_DIR = Path.home() / ".config" / "instaloader"
MAX_IG      = 15    # 인스타 최대 수집
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


def ig_fetch_posts(session, account, max_posts=50):
    r = session.get(
        f"https://www.instagram.com/api/v1/users/web_profile_info/?username={account}",
        headers=IG_HEADERS, timeout=15
    )
    if r.status_code != 200:
        print(f"  프로필 API 실패: {r.status_code}")
        return {}
    edges = r.json()["data"]["user"]["edge_owner_to_timeline_media"]["edges"]
    posts = {}
    for edge in edges[:max_posts]:
        node = edge["node"]
        sc = node["shortcode"]
        if node.get("__typename") == "GraphSidecar":
            children = node.get("edge_sidecar_to_children", {}).get("edges", [])
            img_url = children[0]["node"]["display_url"] if children else node["display_url"]
        else:
            img_url = node["display_url"]
        cap_edges = node.get("edge_media_to_caption", {}).get("edges", [])
        posts[sc] = {
            "img_url": img_url,
            "caption": cap_edges[0]["node"]["text"] if cap_edges else "",
        }
    return posts


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
        feed = ig_fetch_posts(ig_session, account)
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
                time.sleep(0.5)  # 이미지 다운로드 사이 0.5초
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
    place_id = get_place_id(query)
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

    candidates = [
        f"https://m.place.naver.com/restaurant/{place_id}/photo/business",
        f"https://m.place.naver.com/cafe/{place_id}/photo/business",
        f"https://m.place.naver.com/place/{place_id}/photo/business",
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=MOBILE_UA,
            viewport={"width": 390, "height": 844},
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
        )
        page.on("response", on_response)

        success = False
        used_url = candidates[0]
        for url in candidates:
            page.goto(url, wait_until="load", timeout=20000)
            page.wait_for_timeout(2000)
            title = page.title()
            if "찾을 수 없" not in title and "없는 페이지" not in title:
                used_url = url
                success = True
                break

        if success:
            for _ in range(8):
                page.evaluate("window.scrollBy(0, 600)")
                page.wait_for_timeout(600)

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


# ── HTML 생성/업데이트 ────────────────────────────────────────────

def card_ig(item, spot, slug):
    post_id  = item["post_id"]
    dir_name = spot["dir"]
    account  = spot["account"]
    web_path = f"/ref/images/{slug}/{dir_name}/{post_id}.jpg"
    return f"""<div class="card ig-card">
  <div class="img-wrap">
    <img src="{web_path}" loading="lazy" onerror="this.closest('.card').style.display='none'">
    <span class="badge" style="background:#e1306c">Instagram</span>
    <a class="dl-btn" href="{web_path}" download="{dir_name}_{post_id}.jpg">⬇ JPG</a>
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
    <a class="dl-btn" href="{web_path}" download="{dir_name}_{fname}">⬇ JPG</a>
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
        google_cards = "".join(card_google(i, spot, slug) for i in saved.get("google", []))
        manual_cards = "".join(card_manual(i, spot, slug) for i in saved.get("manual", []))

        sections.append(SECTION_TMPL.format(
            dir=dir_name,
            color=spot.get("color", "#333"),
            name=spot["name"],
            addr=spot.get("addr", ""),
            ig_cards=ig_cards,
            naver_cards=naver_cards + google_cards,
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

    slug       = sys.argv[1]
    force_login = "--login" in sys.argv
    config     = load_config(slug)

    # 인스타 필요 여부 확인
    needs_ig = any(
        "instagram" in (s.get("type") if isinstance(s.get("type"), list) else [s.get("type", "")])
        or s.get("type") == "instagram"
        for s in config["spots"]
    )
    ig_session = ig_login(force=force_login) if needs_ig else None

    all_saved = {}

    for spot in config["spots"]:
        dir_name = spot["dir"]
        types    = spot.get("type", [])
        if isinstance(types, str):
            types = [types]

        print(f"\n{'─'*40}")
        print(f"{spot['name']}")
        all_saved[dir_name] = {}

        if "instagram" in types and ig_session:
            result = scrape_instagram(spot, slug, ig_session)
            all_saved[dir_name]["instagram"] = result
            time.sleep(5)  # rate-limit 방지: 계정 사이 5초

        if "naver_place" in types:
            result = scrape_naver(spot, slug)
            all_saved[dir_name]["naver"] = result
            time.sleep(3)

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
        goo_n = len(d.get("google", []))
        man_n = len(d.get("manual", []))
        print(f"  {spot['name']}: IG {ig_n}장 / 네이버 {nav_n}장 / Google {goo_n}장 / manual {man_n}장")

    build_html(config, all_saved, slug)

    print(f"""
배포:
  git add references/ articles/ && git commit -m "Add {slug} images" && git push

URL: https://imagetracker-nine.vercel.app/ref/{slug}
""")


if __name__ == "__main__":
    main()
