"""
Instagram 이미지 스크래퍼 — beer_summer.html용
사용법:
  python3 scrape_ig.py --login   # 최초 1회 로그인
  python3 scrape_ig.py           # 이후 자동 실행
"""

import sys
import re
import json
import getpass
import requests
import instaloader
from pathlib import Path
from urllib.parse import quote

SESSION_DIR = Path.home() / ".config" / "instaloader"
IMAGES_DIR = Path("references/images/beer_summer")
HTML_FILE = Path("references/beer_summer.html")
MAX_IMAGES = 15

SPOTS = [
    {"account": "seoulgypsyfermenteria", "name": "서울집시 퍼멘테리아", "color": "#1e3a1e"},
    {"account": "ruf_pub",               "name": "루프 RUF",           "color": "#1a2c4a"},
    {"account": "heywave_sindang",        "name": "헤이웨이브",          "color": "#4a2000"},
    {"account": "ggeek_beer",             "name": "끽비어 컴퍼니",       "color": "#2a1a4a"},
    {"account": "kokkiri_brewery",        "name": "코끼리 브루어리",      "color": "#1a3a2a"},
]

IG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
    "X-IG-App-ID": "936619743392459",
}


def load_session():
    sessions = list(SESSION_DIR.glob("session-*"))
    if not sessions:
        return None, None
    L = instaloader.Instaloader(quiet=True)
    saved = sessions[0]
    uname = saved.name.replace("session-", "")
    L.load_session_from_file(uname, str(saved))
    print(f"세션 로드: @{uname}")
    return L, uname


def do_login():
    print("=" * 50)
    print("Instagram 로그인 (최초 1회 / 비밀번호 저장 안 함)")
    print("=" * 50)
    username = input("Instagram 아이디: ").strip()
    password = getpass.getpass("비밀번호: ")
    L = instaloader.Instaloader(quiet=True)
    try:
        L.login(username, password)
    except instaloader.exceptions.TwoFactorAuthRequiredException:
        code = input("2FA 코드: ").strip()
        L.two_factor_login(code)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    path = str(SESSION_DIR / f"session-{username}")
    L.save_session_to_file(path)
    print(f"세션 저장 완료: {path}")
    return L


def get_profile_posts(session, account, max_images=MAX_IMAGES):
    """web_profile_info API로 최근 포스트 이미지 URL 수집"""
    r = session.get(
        f"https://www.instagram.com/api/v1/users/web_profile_info/?username={account}",
        headers=IG_HEADERS, timeout=15
    )
    if r.status_code != 200:
        print(f"  API 실패: {r.status_code}")
        return []

    edges = r.json()["data"]["user"]["edge_owner_to_timeline_media"]["edges"]
    results = []
    for edge in edges[:max_images]:
        node = edge["node"]
        shortcode = node["shortcode"]
        post_url = f"https://www.instagram.com/p/{shortcode}/"

        # 슬라이드면 첫 장, 아니면 display_url
        if node.get("__typename") == "GraphSidecar":
            children = node.get("edge_sidecar_to_children", {}).get("edges", [])
            img_url = children[0]["node"]["display_url"] if children else node["display_url"]
        else:
            img_url = node["display_url"]

        results.append({"img_url": img_url, "post_id": shortcode, "post_url": post_url})
        print(f"  [{len(results)}] ✓  {shortcode}")

    return results


def scrape_account(session, account):
    print(f"\n@{account} 스크래핑 중...")
    try:
        results = get_profile_posts(session, account)
        print(f"  → {len(results)}장 수집")
        return results
    except Exception as e:
        print(f"  실패 ({type(e).__name__}): {e}")
        return []


def download_images(account, results):
    dest = IMAGES_DIR / account
    dest.mkdir(parents=True, exist_ok=True)
    for f in dest.glob("*.jpg"):
        f.unlink()

    saved = []
    dl_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://www.instagram.com/",
    }
    for item in results:
        filepath = dest / f"{item['post_id']}.jpg"
        try:
            r = requests.get(item["img_url"], headers=dl_headers, timeout=20)
            r.raise_for_status()
            filepath.write_bytes(r.content)
            saved.append({"path": str(filepath), "post_id": item["post_id"], "post_url": item["post_url"]})
            print(f"  저장: {filepath.name} ({len(r.content)//1024}KB)")
        except Exception as e:
            print(f"  다운로드 실패 {item['post_id']}: {e}")
    return saved


def card_html(saved_item, account):
    post_id = saved_item["post_id"]
    post_url = saved_item["post_url"]
    web_path = f"/ref/images/beer_summer/{account}/{post_id}.jpg"
    enc = quote(web_path, safe="")
    return f"""<div class="card ig-card">
      <div class="img-wrap">
        <img src="{web_path}" loading="lazy" onerror="this.closest('.card').style.display='none'">
        <span class="badge" style="background:#e1306c">Instagram</span>
        <a class="dl-btn" href="/api/download?url={enc}&filename={account}_{post_id}" download>⬇ JPG</a>
      </div>
      <div class="meta">
        <p class="attr">사진/ @{account}</p>
        <a class="src" href="{post_url}" target="_blank">출처 확인 →</a>
      </div>
    </div>"""


def update_html(scraped_counts, all_saved):
    content = HTML_FILE.read_text(encoding="utf-8")
    for spot in SPOTS:
        account = spot["account"]
        items = all_saved.get(account, [])
        if not items:
            continue
        cards = "".join(card_html(item, account) for item in items)
        ig_block = f'<div class="ig-grid ig-{account}">{cards}</div>'
        pat = rf'<div class="ig-grid ig-{re.escape(account)}">.*?</div>'
        if re.search(pat, content, flags=re.DOTALL):
            content = re.sub(pat, ig_block, content, flags=re.DOTALL)
        else:
            content = re.sub(
                rf'(instagram\.com/{re.escape(account)}/[^"]*"[^>]*>.*?출처 확인 →</a>\s*</div>\s*</div>\s*)(<div class="grid">)',
                rf'\1{ig_block}\n      \2',
                content, flags=re.DOTALL
            )
    total_ig = sum(scraped_counts.values())
    orig = re.search(r'총 (\d+)장', content)
    if orig:
        base = int(orig.group(1))
        content = re.sub(r'총 \d+장[^<]*', f'총 {base + total_ig}장 (Instagram {total_ig}장 포함)', content, count=1)
    HTML_FILE.write_text(content, encoding="utf-8")
    print(f"\nHTML 업데이트 완료!")


def main():
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    if "--login" in sys.argv:
        L = do_login()
        ig_session = L.context._session
    else:
        L, username = load_session()
        if not L:
            print("저장된 세션 없음 — 먼저 로그인하세요: python3 scrape_ig.py --login")
            sys.exit(1)
        ig_session = L.context._session

    all_saved = {}
    scraped_counts = {}
    for spot in SPOTS:
        account = spot["account"]
        results = scrape_account(ig_session, account)
        if results:
            saved = download_images(account, results)
            scraped_counts[account] = len(saved)
            all_saved[account] = saved
        else:
            scraped_counts[account] = 0
            all_saved[account] = []

    print("\n=== 수집 결과 ===")
    total = sum(scraped_counts.values())
    for spot in SPOTS:
        print(f"  @{spot['account']}: {scraped_counts.get(spot['account'], 0)}장")
    print(f"  합계: {total}장")

    if total > 0:
        update_html(scraped_counts, all_saved)
        print("\n배포:")
        print("  git add references/ && git commit -m 'Add Instagram images' && git push")


if __name__ == "__main__":
    main()
