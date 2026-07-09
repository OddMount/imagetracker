#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
인스타 이미지 수집 — 유저 크롬 프로필(로그인됨) 직접 사용 방식.
instaloader 세션/직접 API가 인스타 락다운으로 막혔을 때의 '되는' 방법.

핵심 원리:
  - 크롬 프로필 중 인스타 로그인된 것을 자동 탐지 (Local State + Cookies sqlite에서 sessionid)
  - 크롬이 켜져 있으면 프로필이 잠기므로 → 쿠키만 임시폴더로 복사
  - Playwright를 channel="chrome"(진짜 크롬)로 그 프로필로 실행
    → 크롬이 자기 쿠키를 키체인 ACL로 스스로 복호화 (browser_cookie3처럼 막히지 않음)
    → '유저가 브라우저 켜는 것과 동일'하게 로그인 상태로 진입
  - 프로필 페이지 렌더 후 scontent 이미지(피드 640px) 수집·다운로드

사용법:
  python3 scrape_ig_chrome.py <outdir> <account1> [account2 ...]
  예) python3 scrape_ig_chrome.py references/images/monsoon plainpod wechik.official
"""
import os, sys, json, sqlite3, shutil, tempfile, time, requests
from pathlib import Path
from playwright.sync_api import sync_playwright

CHROME = os.path.expanduser("~/Library/Application Support/Google/Chrome")
N_PER = int(os.environ.get("IG_N", "6"))
DL_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def find_logged_in_profile():
    """인스타 sessionid 쿠키가 있는 크롬 프로필 폴더를 찾는다."""
    try:
        ls = json.load(open(os.path.join(CHROME, "Local State")))
        profiles = list(ls.get("profile", {}).get("info_cache", {}).keys())
    except Exception:
        profiles = ["Default", "Profile 1", "Profile 2", "Profile 3", "Profile 4"]
    for d in profiles:
        for sub in ["Network/Cookies", "Cookies"]:
            cf = os.path.join(CHROME, d, sub)
            if not os.path.exists(cf):
                continue
            try:
                tmp = tempfile.mktemp(suffix=".db")
                shutil.copy(cf, tmp)
                con = sqlite3.connect(tmp); cur = con.cursor()
                cur.execute("SELECT 1 FROM cookies WHERE host_key LIKE '%instagram.com%' AND name='sessionid' LIMIT 1")
                hit = cur.fetchone()
                con.close(); os.remove(tmp)
                if hit:
                    return d
            except Exception:
                pass
            break
    return None


def stage_profile(profile_dir):
    """잠긴 프로필의 쿠키만 임시 user-data-dir로 복사 (Default 프로필로)."""
    tmp = tempfile.mkdtemp(prefix="pw-chrome-")
    os.makedirs(os.path.join(tmp, "Default", "Network"), exist_ok=True)
    shutil.copy(os.path.join(CHROME, "Local State"), os.path.join(tmp, "Local State"))
    for f in ["Network/Cookies", "Network/Cookies-journal", "Cookies", "Preferences", "Secure Preferences"]:
        s = os.path.join(CHROME, profile_dir, f)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(tmp, "Default", f))
    return tmp


def big(u):
    return "cdninstagram" in u and (".jpg" in u or ".webp" in u) and not any(
        x in u for x in ["s150x150", "s320x320", "s640x640", "profile_pic"])


def scrape(accounts, outdir):
    prof = find_logged_in_profile()
    if not prof:
        print("❌ 인스타 로그인된 크롬 프로필을 못 찾음. 크롬에서 인스타 로그인 확인 필요.")
        return
    print(f"✅ 인스타 로그인 프로필: {prof}")
    tmp = stage_profile(prof)
    out = Path(outdir)
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                tmp, channel="chrome", headless=True,
                args=["--no-first-run", "--no-default-browser-check", "--disable-background-networking"])
            pg = ctx.new_page()
            pg.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=40000)
            pg.wait_for_timeout(3000)
            if "accounts/login" in pg.url:
                print(f"❌ 로그인 안 됨 (세션 만료?) — {pg.url}"); ctx.close(); return
            print("✅ 로그인 상태 진입")
            for acct in accounts:
                d = out / acct.replace(".", "_"); d.mkdir(parents=True, exist_ok=True)
                imgs = []
                def h(resp, _b=imgs):
                    if big(resp.url): _b.append(resp.url)
                pg.on("response", h)
                try:
                    pg.goto(f"https://www.instagram.com/{acct}/", wait_until="load", timeout=40000)
                    pg.wait_for_timeout(3500)
                    pg.mouse.wheel(0, 3000); pg.wait_for_timeout(3000)
                except Exception as e:
                    print(f"  {acct} load-err {str(e)[:50]}")
                pg.remove_listener("response", h)
                try:
                    imgs += [u for u in pg.eval_on_selector_all("article img", "e=>e.map(x=>x.src)") if big(u)]
                except Exception:
                    pass
                seen = []
                for u in imgs:
                    if u.split("?")[0] not in [s.split("?")[0] for s in seen]:
                        seen.append(u)
                ok = 0
                for i, u in enumerate(seen[:N_PER], 1):
                    try:
                        r = requests.get(u, headers=DL_UA, timeout=20)
                        if r.status_code == 200 and len(r.content) > 12000 and r.content[:1] != b"<":
                            (d / f"{acct.replace('.', '_')}_ig_{i}.jpg").write_bytes(r.content); ok += 1
                    except Exception:
                        pass
                print(f"  @{acct}: {ok}컷 저장 (후보 {len(seen)})")
            ctx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("DONE")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python3 scrape_ig_chrome.py <outdir> <account1> [account2 ...]")
        sys.exit(1)
    scrape(sys.argv[2:], sys.argv[1])
