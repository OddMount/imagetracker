#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
인스타 이미지 수집 — 유저 크롬 프로필(로그인됨) 직접 사용 방식.
instaloader 세션/직접 API가 인스타 락다운으로 막혔을 때의 '되는' 방법.

핵심 원리:
  - 크롬 프로필 중 인스타 로그인된 것을 자동 탐지 (Local State + Cookies sqlite의 sessionid)
  - 크롬이 켜져 있으면 프로필이 잠기므로 → 쿠키만 임시폴더로 복사
  - Playwright를 channel="chrome"(진짜 크롬)로 그 프로필로 실행
    → 크롬이 자기 쿠키를 키체인 ACL로 스스로 복호화 (browser_cookie3처럼 막히지 않음)
    → '유저가 브라우저 켜는 것과 동일'하게 로그인 상태로 진입
  - 프로필 렌더 중 JSON 응답 인터셉트 → 게시물 caption + 고해상(image_versions2 1080) 수집
  - ★키워드 연관성 점수로 정렬해 상위 N개 선별 (단순 최신순 아님). scontent(640)는 fallback.

사용법:
  python3 scrape_ig_chrome.py <outdir> <account[:kw+kw+...]> [account2[:kw...] ...]
  예) python3 scrape_ig_chrome.py references/images/monsoon \\
        saengong_official:제습+습기+옷장 wechik.official:세탁+섬유유연제+냄새
  (계정 뒤 :키워드 생략하면 최신순으로 가져옴)
  IG_N 환경변수로 개수 조절 (기본 6)
"""
import os, sys, json, sqlite3, shutil, tempfile, time, requests
from pathlib import Path
from playwright.sync_api import sync_playwright

CHROME = os.path.expanduser("~/Library/Application Support/Google/Chrome")
N_PER = int(os.environ.get("IG_N", "6"))
SCROLLS = int(os.environ.get("IG_SCROLLS", "4"))   # 후보 더 모으려 스크롤 횟수
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


def walk_json(obj, out):
    """JSON에서 게시물 노드(shortcode + 이미지 + caption)를 재귀 추출."""
    if isinstance(obj, dict):
        code = obj.get("code") or obj.get("shortcode")
        # caption
        cap = ""
        c = obj.get("caption")
        if isinstance(c, dict):
            cap = c.get("text", "") or ""
        elif isinstance(c, str):
            cap = c
        if not cap:
            edges = (obj.get("edge_media_to_caption", {}) or {}).get("edges", [])
            if edges:
                cap = edges[0].get("node", {}).get("text", "") or ""
        # 고해상 이미지 URL
        du = obj.get("display_url")
        if not du:
            iv = obj.get("image_versions2")
            if isinstance(iv, dict) and iv.get("candidates"):
                du = iv["candidates"][0].get("url")
        if code and du and "cdninstagram" in du:
            prev = out.get(code)
            # caption 있는 쪽 우선 보존
            if not prev or (cap and not prev.get("caption")):
                out[code] = {"url": du, "caption": cap}
        for v in obj.values():
            walk_json(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_json(v, out)


def pick(cands, keywords, n):
    """키워드 연관성 점수순 선별. 키워드 없으면 입력 순서(최신순) 유지."""
    items = list(cands)  # [(order, url, caption)]
    if keywords:
        kws = [k.lower() for k in keywords if k]
        def score(it):
            cap = (it[2] or "").lower()
            return sum(cap.count(k) for k in kws)
        scored = sorted(items, key=lambda it: (-score(it), it[0]))
        relevant = [it for it in scored if score(it) > 0]
        chosen = relevant[:n]
        if len(chosen) < n:  # 부족하면 최신순으로 채움
            for it in items:
                if it not in chosen:
                    chosen.append(it)
                if len(chosen) >= n:
                    break
        return chosen[:n]
    return items[:n]


def scrape(specs, outdir):
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

            for spec in specs:
                acct = spec.split(":")[0]
                keywords = spec.split(":")[1].split("+") if ":" in spec else []
                d = out / acct.replace(".", "_"); d.mkdir(parents=True, exist_ok=True)

                nodes = {}          # code -> {url, caption}  (JSON, 고해상+캡션)
                scont = []          # fallback scontent URLs (640, 캡션 없음)

                def on_resp(resp, _n=nodes, _s=scont):
                    u = resp.url
                    if big(u):
                        _s.append(u)
                    ct = (resp.headers or {}).get("content-type", "")
                    if "json" in ct and ("instagram.com" in u):
                        try:
                            walk_json(resp.json(), _n)
                        except Exception:
                            pass
                pg.on("response", on_resp)
                try:
                    pg.goto(f"https://www.instagram.com/{acct}/", wait_until="load", timeout=40000)
                    pg.wait_for_timeout(3000)
                    for _ in range(SCROLLS):
                        pg.mouse.wheel(0, 2600); pg.wait_for_timeout(1800)
                except Exception as e:
                    print(f"  {acct} load-err {str(e)[:50]}")
                pg.remove_listener("response", on_resp)

                # 후보 구성: JSON(캡션·고해상) 우선, 없으면 scontent
                cands = []
                order = 0
                seen_url = set()
                for code, info in nodes.items():
                    k = info["url"].split("?")[0]
                    if k in seen_url:
                        continue
                    seen_url.add(k)
                    cands.append((order, info["url"], info.get("caption", "")))
                    order += 1
                if len(cands) < N_PER:  # JSON 부족 → scontent 보강(캡션 없음)
                    for u in scont:
                        k = u.split("?")[0]
                        if k in seen_url:
                            continue
                        seen_url.add(k)
                        cands.append((order, u, ""))
                        order += 1

                chosen = pick(cands, keywords, N_PER)
                ok = 0
                for i, (_o, u, _c) in enumerate(chosen, 1):
                    try:
                        r = requests.get(u, headers=DL_UA, timeout=20)
                        if r.status_code == 200 and len(r.content) > 12000 and r.content[:1] != b"<":
                            (d / f"{acct.replace('.', '_')}_ig_{i}.jpg").write_bytes(r.content); ok += 1
                    except Exception:
                        pass
                tag = f"(키워드 {keywords} 연관순)" if keywords else "(최신순)"
                print(f"  @{acct}: {ok}컷 저장 {tag} · 후보 {len(cands)}(JSON {len(nodes)})")
            ctx.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("DONE")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python3 scrape_ig_chrome.py <outdir> <account[:kw+kw+...]> ...")
        sys.exit(1)
    scrape(sys.argv[2:], sys.argv[1])
