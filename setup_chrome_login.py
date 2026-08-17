#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_ig_chrome.py용 전용 크롬 프로필에 인스타그램 로그인.
최초 1회, 또는 세션 만료 시 재실행.

이 프로필(~/.config/imagetracker-chrome-profile)은 scrape_ig_chrome.py가
매번 직접 재사용하므로, 유저 데일리 크롬 프로필의 쿠키를 복사해오는 방식과 달리
크롬의 세션 쿠키 보호(2024+ 키체인 바인딩)에 걸리지 않는다.

사용법:
  python3 setup_chrome_login.py
브라우저 창이 뜨면 인스타그램에 로그인. sessionid 쿠키가 감지되면 자동으로 창이 닫힌다.
"""
import os, time
from playwright.sync_api import sync_playwright

PROFILE_DIR = os.path.expanduser("~/.config/imagetracker-chrome-profile")


def main():
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR, channel="chrome", headless=False,
            args=["--no-first-run", "--no-default-browser-check"])
        pg = ctx.new_page()
        pg.goto("https://www.instagram.com/accounts/login/", timeout=60000)
        print("브라우저 창이 열렸습니다. 인스타그램에 로그인해주세요. (최대 10분 대기)")

        deadline = time.time() + 600
        logged_in = False
        while time.time() < deadline:
            time.sleep(3)
            names = [c["name"] for c in ctx.cookies() if "instagram.com" in c["domain"]]
            if "sessionid" in names:
                logged_in = True
                break

        if logged_in:
            time.sleep(3)  # 세션 데이터 디스크 flush 대기
            ctx.close()
            print("✅ 로그인 완료 — 세션 저장됨")
        else:
            ctx.close()
            print("❌ 10분 내 로그인 감지 안 됨 — 타임아웃")


if __name__ == "__main__":
    main()
