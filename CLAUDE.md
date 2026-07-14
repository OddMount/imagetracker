# Imagetracker — Claude Code 컨텍스트

하퍼스 바자 코리아 에디터(최노아)의 아티클 이미지 레퍼런스 페이지 생성 툴.
배포 URL: https://imagetracker-nine.vercel.app/ref/{slug}

---

## 파일 구조

```
imagetracker/
  CLAUDE.md              ← 이 파일
  scrape.py              ← 범용 실행 진입점 (python3 scrape.py {slug})
  scrape_ig_curry.py     ← 커리 전용 (레거시)
  scrape_naver_curry.py  ← 커리 전용 (레거시)
  app.py                 ← Flask/Vercel 라우팅
  articles/              ← 아티클별 config JSON
    curry.json
  references/
    {slug}.html          ← 생성된 레퍼런스 페이지
    images/{slug}/
      {dir}/             ← 인스타 이미지 (.jpg, post_id 파일명)
      {dir}/naver/       ← 네이버 이미지 (naver_01.jpg ...)
```

---

## 트리거 명령어

사용자가 아래 형식으로 메시지를 보내면 Claude가 끝까지 자동 처리:

```
/수집 {슬러그}
{원고 전문}
```

**Claude 자동 실행 순서 (사용자 개입 없이):**
1. 원고 파싱 → 업체명/인스타계정/소스타입/주소/키워드 추출
2. `articles/{slug}.json` 생성
3. `python3 scrape.py {slug}` 실행 (Bash)
4. `git add references/ articles/ && git commit -m "Add {slug}" && git push` (Bash)
5. 완료 후 URL 반환: `https://imagetracker-nine.vercel.app/ref/{slug}`

**slug 규칙:** 영문 소문자+하이픈, 아티클 주제 한두 단어
예) 레인코트 → `raincoat`, 여름 맥주 → `beer-summer`, 핀란드 사우나 → `sauna`

**원고에 인스타 계정이 명시 안 된 경우:** Claude가 업체명으로 추측해서 넣고 진행, 틀리면 수정 요청

---

## 이미지 소스 타입 분류

하퍼스 바자 크레딧 표기 → 소스 타입 매핑:

| 크레딧 원문 | type |
|---|---|
| 업체 공식 인스타그램, 업체 SNS, 업체 인스타그램, @계정명 | `instagram` |
| 네이버 플레이스 업체 제공, 네이버 업체 등록 사진, 네이버 플레이스 | `naver_place` |
| 브랜드명 제공, 출판사 제공, 공식 홈페이지 | `manual` |
| Gettyimages, 게티 이미지 | `manual` (유료, URL 직접 입력) |
| 에디터 제공, 유튜브 캡처 | `manual` (직접 업로드) |

**저작권 원칙:**
- `instagram`: 업체 공식 계정만 허용 (개인 블로그/방문자 리뷰 금지)
- `naver_place`: ldb-phinf.pstatic.net URL만 허용 (업체제공 확인 필수)
- `manual`: 에디터가 직접 URL 제공한 것만

### 보강 소스 (업체제공이 얇을 때 — 전부 "⚠️ 저작권 확인 필요" 표시로 에디터 판단용)
- `diningcode`: 다이닝코드 음식 사진(750px, CDN `d12zq4w4guyljn.cloudfront.net` `_photo_`). `diningcode_query` 사용. 파일명 타임스탬프로 **업로드 나이 라벨**을 카드에 표시(오래된 순 정렬). ⚠️ 프로필엔 최근 사진 위주로 떠서 3년+ 사진은 적음.
- `google_images`: Bing 이미지 검색(구글은 스크랩 차단 심해 Bing 엔진). `query`(음식 위주로) 사용. 촬영일 필터 불가.
- 이 둘의 카드는 ⚠️ 배지 + 출처링크가 붙어 에디터가 직접 골라 씀. 업체제공(네이버/카카오)과 시각적으로 분리 렌더됨.

---

## articles/{slug}.json 구조

```json
{
  "slug": "curry",
  "title": "평범한 카레는 없다, 서울 카레 맛집 5",
  "spots": [
    {
      "name": "커리하우스 라사",
      "dir": "rasa_seoul",
      "type": "instagram",
      "account": "rasa_seoul",
      "naver_query": "합정 커리하우스 라사",
      "addr": "서울 마포구 포은로2가길 6 B102호",
      "color": "#3d1f00",
      "keywords": "커리 카레 향신료 파니르"
    },
    {
      "name": "브랜드 예시",
      "dir": "brand_dir",
      "type": "manual",
      "urls": ["https://..."]
    }
  ]
}
```

- `type: instagram` → account 필수, keywords 선택 (관련 사진 우선 정렬)
- `type: naver_place` → naver_query 필수
- `type: manual` → urls 배열 (비어있으면 스킵, 나중에 추가 가능)
- 한 업체가 인스타+네이버 둘 다 필요하면 type을 `["instagram", "naver_place"]`로 배열로

---

## 스크래퍼 설정값

- 인스타 최대 수집: 15장 (관련도 점수순 정렬)
- 네이버 최대 수집: 12장 (100KB 이상만, 썸네일 제외)
- 세션 파일: `~/.config/instaloader/session-*`
- 이미지 저장: `references/images/{slug}/{dir}/`

---

## 기존 아티클 현황

| slug | HTML | 이미지 |
|---|---|---|
| curry | references/curry.html | references/images/curry/ |
| beer_summer | references/beer_summer.html | references/images/beer_summer/ |

---

## 주의사항

- `update_html()`의 naver-grid 블록 교체는 regex 대신 str.find() 방식 사용 (regex는 중첩 div에서 오작동)
- 인스타 세션 만료 시: `python3 scrape_ig.py --login` 재로그인
- Vercel 환경변수 불필요 (이미지는 정적 파일로 서빙)
