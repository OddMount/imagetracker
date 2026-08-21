#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
인스타 이미지 수집 — 전용 크롬 프로필(로그인됨) 직접 사용 방식.
instaloader 세션/직접 API가 인스타 락다운으로 막혔을 때의 '되는' 방법.

핵심 원리:
  - ~/.config/imagetracker-chrome-profile 라는 스크래퍼 전용 크롬 프로필을 사용.
    (최초 1회만 python3 setup_chrome_login.py 로 그 프로필에 직접 로그인해두면 됨)
  - 유저 데일리 크롬 프로필의 쿠키를 복사해오는 방식은 더 이상 안 씀 —
    2024+ 크롬 보안 강화로 세션 쿠키(sessionid)가 복사본에서는 복호화가 안 됨
    (원본 프로필 경로에 묶인 키체인 보호 때문). 대신 이 스크립트가 처음부터 끝까지
    같은 프로필을 직접 관리하면 복사 없이 정상적으로 로그인 상태가 유지됨.
  - 프로필 렌더 중 JSON 응답 인터셉트 → 게시물 caption + 고해상(image_versions2 1080) 수집
  - ★키워드 연관성 점수로 정렬해 상위 N개 선별 (단순 최신순 아님). scontent(640)는 fallback.

사용법:
  python3 scrape_ig_chrome.py <outdir> <account[:kw+kw+...]> [account2[:kw...] ...]
  예) python3 scrape_ig_chrome.py references/images/monsoon \\
        saengong_official:제습+습기+옷장 wechik.official:세탁+섬유유연제+냄새
  (계정 뒤 :키워드 생략하면 최신순으로 가져옴)
  IG_N 환경변수로 개수 조절 (기본 6)

  세션 만료 시: python3 setup_chrome_login.py 로 재로그인
"""
import os, sys, requests
from pathlib import Path
from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.expanduser("~/.config/imagetracker-chrome-profile")
N_PER = int(os.environ.get("IG_N", "6"))
SCROLLS = int(os.environ.get("IG_SCROLLS", "4"))   # 후보 더 모으려 스크롤 횟수
DL_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def is_ig_cdn(u):
    # 인스타 CDN 호스트명이 cdninstagram.com 계열과 fbcdn.net 계열(instagram.*.fna.fbcdn.net)
    # 둘 다로 뜸 — 하나만 체크하면 최신 게시물이 통째로 걸러짐.
    return "cdninstagram" in u or "fbcdn.net" in u


def big(u):
    return is_ig_cdn(u) and (".jpg" in u or ".webp" in u) and not any(
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
        if code and du and is_ig_cdn(du):
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
    if not os.path.isdir(PROFILE_DIR):
        print(f"❌ 전용 크롬 프로필이 없음. 먼저 python3 setup_chrome_login.py 로 로그인하세요.")
        return
    out = Path(outdir)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, channel="chrome", headless=True,
            args=["--no-first-run", "--no-default-browser-check", "--disable-background-networking"])
        pg = ctx.new_page()
        pg.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=40000)
        pg.wait_for_timeout(3000)
        if "accounts/login" in pg.url:
            print(f"❌ 로그인 안 됨 (세션 만료?) — python3 setup_chrome_login.py 로 재로그인하세요."); ctx.close(); return
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
                if ("json" in ct or "javascript" in ct) and ("instagram.com" in u):
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
    print("DONE")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python3 scrape_ig_chrome.py <outdir> <account[:kw+kw+...]> ...")
        sys.exit(1)
    scrape(sys.argv[2:], sys.argv[1])
