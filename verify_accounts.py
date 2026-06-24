"""
인스타 계정 검증 스크립트
사용법: python3 verify_accounts.py {slug}
"""

import sys, json, time, re
import instaloader, requests
from pathlib import Path

SESSION_DIR = Path.home() / ".config" / "instaloader"
IG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
    "X-IG-App-ID": "936619743392459",
}

def load_session():
    sessions = list(SESSION_DIR.glob("session-*"))
    if not sessions:
        print("세션 없음. python3 scrape.py {slug} --login 먼저 실행하세요")
        sys.exit(1)
    L = instaloader.Instaloader(quiet=True)
    uname = sessions[0].name.replace("session-", "")
    L.load_session_from_file(uname, str(sessions[0]))
    print(f"세션 로드: @{uname}\n")
    return L.context._session

def fetch_profile(session, account):
    r = session.get(
        f"https://www.instagram.com/api/v1/users/web_profile_info/?username={account}",
        headers=IG_HEADERS, timeout=15
    )
    if r.status_code != 200:
        return None, r.status_code
    data = r.json()
    if "data" not in data or not data["data"].get("user"):
        return None, data.get("message", "unknown error")
    user = data["data"]["user"]
    return {
        "full_name": user.get("full_name", ""),
        "biography": user.get("biography", ""),
        "is_private": user.get("is_private", False),
        "post_count": user.get("edge_owner_to_timeline_media", {}).get("count", 0),
        "followers": user.get("edge_followed_by", {}).get("count", 0),
        "ig_url": f"https://www.instagram.com/{account}/",
    }, 200

def check_addr(bio, full_name, addr):
    combined = (bio + " " + full_name).lower()
    words = re.findall(r'[가-힣]{3,}', addr)
    matches = [w for w in words if w in combined]
    return matches

def main():
    if len(sys.argv) < 2:
        print("사용법: python3 verify_accounts.py {slug}")
        sys.exit(1)

    slug = sys.argv[1]
    path = Path(f"articles/{slug}.json")
    if not path.exists():
        print(f"articles/{slug}.json 없음")
        sys.exit(1)

    config = json.loads(path.read_text(encoding="utf-8"))
    spots = [s for s in config["spots"] if s.get("account")]

    print(f"{'='*55}")
    print(f"계정 검증: {config.get('title', slug)}")
    print(f"인스타 계정 있는 장소: {len(spots)}개")
    print(f"{'='*55}\n")

    session = load_session()
    ok, warn, fail = 0, 0, 0

    for spot in spots:
        account = spot["account"]
        name = spot["name"]
        addr = spot.get("addr", "")

        print(f"[{name}]  @{account}")
        profile, status = fetch_profile(session, account)

        if profile is None:
            print(f"  ❌ API 실패 ({status})")
            fail += 1
        else:
            bio = profile["biography"]
            full = profile["full_name"]
            priv = "🔒 비공개" if profile["is_private"] else f"팔로워 {profile['followers']:,}  게시물 {profile['post_count']}"
            print(f"  이름: {full or '(없음)'}")
            print(f"  Bio : {bio[:80] or '(없음)'}")
            print(f"  계정: {priv}")

            matches = check_addr(bio, full, addr)
            if matches:
                print(f"  ✅ 주소 일치: {matches}")
                ok += 1
            else:
                print(f"  ⚠️  bio에 주소 키워드 없음 — 직접 확인 필요")
                print(f"      → {profile['ig_url']}")
                warn += 1

        print()
        time.sleep(3)

    print(f"{'='*55}")
    print(f"결과: ✅ {ok}개 확인  ⚠️ {warn}개 요확인  ❌ {fail}개 실패")
    print(f"{'='*55}")

if __name__ == "__main__":
    main()
