#!/usr/bin/env python3
"""
청년 정책 스크래퍼 (최적화 버전)
=====================================
입력 : data_engineering/scraper/target_boards.json
출력 : data_engineering/scraper/scraped_policies.json

지원 시나리오:
  1. action=direct + 목록 URL  → 페이징 순회 → 각 정책 클릭 → 추출
  2. action=click  + target_text → 탭 클릭 → 목록 순회 → 추출
  3. action=direct + 단일 정책 URL → 페이지 자체에서 직접 추출

비용·시간 최적화:
  - 도메인별 CSS 셀렉터 캐싱 → 동일 사이트 = Gemini 1회 호출
  - HTML 전처리 후 전송 → 토큰 최소화
  - 실제 URL 있는 항목은 직접 이동 (JS 클릭보다 빠름)
  - asyncio Semaphore → 지역 단위 병렬 처리
"""

import json
import os
import asyncio
import re
from urllib.parse import urlparse, urlencode, urlparse, parse_qs, urlencode, urlunparse

from playwright.async_api import async_playwright, Page, BrowserContext
from google import genai

# ── 설정 ──────────────────────────────────────────────────────────────────────
BASE_DIR   = "data_engineering/scraper"
INPUT_FILE = os.path.join(BASE_DIR, "target_boards.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "scraped_policies.json")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=GOOGLE_API_KEY)

MAX_PAGES          = 50   # 페이지 최대 순회 수
MAX_ITEMS_PER_PAGE = 50   # 페이지당 최대 항목 수
REGION_CONCURRENCY = 2    # 동시 처리 지역 수 (높이면 차단 위험)
PAGE_WAIT          = 1.5  # 페이지 로드 후 대기(초)
NAV_TIMEOUT        = 20_000  # 페이지 이동 타임아웃(ms)

# ── 공통 JS 스니펫 ─────────────────────────────────────────────────────────────
# 사이드바·노이즈 제거 후 HTML 7000자 추출
# ※ 'aside'(태그)와 '.aside'(클래스) 모두 제거
_TRIM_HTML_JS = """() => {
    let c = document.body.cloneNode(true);
    c.querySelectorAll(
        'script,style,svg,img,nav,footer,header,aside,.aside,' +
        '.gnb,.lnb,.snb,.location,.breadcrumb,.sitemap,.skip,.skip-nav'
    ).forEach(e => e.remove());
    return c.innerHTML.substring(0, 7000);
}"""

# 정책 목록 링크만 선별하는 JS
# - 사이드바·헤더·푸터·검색필터 영역 제외
# - 텍스트 패턴으로 네비게이션/버튼 링크 제외
# - 8자 미만 텍스트는 메뉴로 간주
_GET_LINKS_JS = """() => {
    const BAD = [
        '.aside', 'aside', '#aside', '.sidebar',
        '.snb', '.lnb', '.gnb', '.hnb',
        '.skip', '.skip-nav', '.skip-main', '.skip-wrap',
        '.location', '.breadcrumb',
        'header', 'footer', 'nav',
        '.pagination', '.paging', '#paginationForm',
        '.banner', '.popup', '.modal', '.dimmed',
        '.quick', '.quick-link', '.shortcut',
        '.con-header', '.page-title',
        'fieldset',
    ].join(',');

    const IGNORE_RE = [
        /\\(\\d+[건개항목]\\)/,
        /바로가기/,
        /[열닫]기/,
        /^\\d+페이지|페이지.*이동/,
        /프린트|출력|인쇄/,
        /공유하기|스크랩|관심정책/,
        /로그인|회원가입/,
        /이용약관|개인정보처리방침/,
        /오시는길|찾아오시는/,
        /^https?:\\/\\//,    // 텍스트가 URL 자체인 링크 제외
        / > /,              // "A > B > C" 형식의 breadcrumb 텍스트 제외
    ];
    const IGNORE_EXACT = new Set([
        '신청하기','자세히보기','더보기','전체보기','목록','목록으로',
        '이전','다음','처음','끝','HOME','home','TOP','top',
        '비교하기','정책비교','관심','공유','인쇄','스크랩',
        '신청','접수','확인','닫기',
    ]);

    // 메인 컨텐츠 영역 (우선순위 순)
    const MAIN_CANDIDATES = [
        '#printId','#content','#main','#contents',
        '.con','.cont','.content','.contents',
        '.board-list','.list-wrap','.policy-list','.result-list',
        '[role="main"]','main',
    ];
    let main = null;
    for (const sel of MAIN_CANDIDATES) {
        try {
            const el = document.querySelector(sel);
            if (el) { main = el; break; }
        } catch(e) {}
    }
    if (!main) main = document.body;

    const seen = new Set();
    const results = [];
    for (const a of main.querySelectorAll('a')) {
        if (a.closest(BAD)) continue;
        if (a.offsetWidth === 0 || a.offsetHeight === 0) continue;
        const t = (a.innerText || '').replace(/[\\n\\t]/g,' ').replace(/\\s+/g,' ').trim();
        if (t.length < 8) continue;
        if (/^\\d+$/.test(t)) continue;
        if (IGNORE_RE.some(re => re.test(t))) continue;
        if (IGNORE_EXACT.has(t)) continue;
        if (seen.has(t)) continue;
        seen.add(t);
        results.push({ text: t, href: a.href, raw_href: a.getAttribute('href') || '' });
    }
    return results;
}"""

# 카드 목록 방식 — '자세히보기'/'상세보기' 버튼 카드 정보 수집 JS
# 버튼 개수 + 부모 카드의 제목 텍스트를 함께 반환
_GET_DETAIL_BTN_INFO_JS = """() => {
    const BTN_TEXTS = ['자세히보기', '상세보기', '자세히 보기', '상세 보기'];
    const btns = Array.from(document.querySelectorAll('a')).filter(a => {
        const t = (a.innerText || '').trim();
        return BTN_TEXTS.includes(t) && a.offsetWidth > 0 && a.offsetHeight > 0;
    });
    return btns.map(btn => {
        // 부모 카드(li, article, .card, .item 등)에서 제목 추출
        const card = btn.closest('li, article, .card, .item, .list-item, tr, .policy-item')
                     || btn.parentElement?.parentElement
                     || btn.parentElement;
        let title = '';
        if (card) {
            const titleEl = card.querySelector(
                'strong, h2, h3, h4, .tit, .title, .subject, dt, .policy-name, .name, em'
            );
            title = titleEl ? titleEl.innerText.trim() : '';
            if (!title) {
                // fallback: 카드 내 가장 긴 텍스트 노드
                const texts = Array.from(card.querySelectorAll('*'))
                    .map(el => (el.innerText || '').trim())
                    .filter(t => t.length >= 5 && t !== btn.innerText.trim());
                title = texts.sort((a, b) => b.length - a.length)[0] || '';
            }
        }
        return { title: title.substring(0, 100) };
    });
}"""

# ── 사이드바 필터 단어 (Python 측 추가 보호) ──────────────────────────────────
_IGNORE_TEXTS = frozenset([
    "로그인","회원가입","개인정보","이용약관","오시는길","중앙정부","타지역",
    "캘린더","더보기","전체보기","목록으로","이전","다음","처음","끝",
    "홈","HOME","TOP","인쇄","공유","스크랩","관심",
    "청소년","아동","노인","장애인","어르신",
])

# ── 도메인별 CSS 셀렉터 캐시 ──────────────────────────────────────────────────
_css_cache: dict[str, dict | None] = {}
_css_lock  = asyncio.Lock()


# ──────────────────────────────────────────────────────────────────────────────
# [LLM] CSS 셀렉터 추출 (도메인별 1회)
# ──────────────────────────────────────────────────────────────────────────────
async def get_css_rules(page: Page, domain: str) -> dict | None:
    """
    도메인별로 CSS 셀렉터를 캐싱한다.
    같은 도메인(chungbuk.go.kr 등)은 첫 번째 상세 페이지 방문 시 1회만 Gemini 호출.
    """
    async with _css_lock:
        if domain in _css_cache:
            return _css_cache[domain]

    print(f"   🧠 [LLM] {domain} CSS 셀렉터 추출 (최초 1회)")
    html = await page.evaluate(_TRIM_HTML_JS)

    prompt = f"""아래는 청년 정책 **상세 페이지** HTML이야.
다음 5개 항목을 각각 추출할 수 있는 Playwright CSS Selector를 JSON으로 줘.
항목이 없으면 빈 문자열("")로 줘. 셀렉터는 반드시 실제 DOM에 존재하는 것만 사용해.

HTML:
{html}

응답 JSON:
{{
  "title_sel":   "정책 제목 CSS 셀렉터",
  "content_sel": "지원 내용 CSS 셀렉터",
  "target_sel":  "지원 대상 CSS 셀렉터",
  "period_sel":  "신청 기간 CSS 셀렉터",
  "method_sel":  "신청 방법 CSS 셀렉터"
}}
"""
    rules: dict | None = None
    try:
        res = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        rules = json.loads(res.text)
        # 빈 셀렉터 키는 제거
        rules = {k: v for k, v in rules.items() if v}
        print(f"   🎯 [셀렉터 획득] {rules}")
    except Exception as e:
        print(f"   ❌ [LLM 오류] {e}")
        rules = None

    async with _css_lock:
        _css_cache[domain] = rules
    return rules


# ──────────────────────────────────────────────────────────────────────────────
# [LLM] 폴백 — 전체 내용 직접 파싱 (CSS 규칙 없을 때)
# ──────────────────────────────────────────────────────────────────────────────
async def extract_with_llm(page: Page, source_url: str) -> dict:
    print(f"   🧠 [LLM 폴백] 전체 내용 직접 파싱")
    html = await page.evaluate(_TRIM_HTML_JS)
    prompt = f"""아래는 청년 정책 상세 페이지 HTML이야.
다음 5개 항목을 추출해서 JSON으로 줘. 항목이 없으면 빈 문자열로 줘.

HTML:
{html}

응답 JSON:
{{
  "title":   "정책 제목",
  "content": "지원 내용",
  "target":  "지원 대상",
  "period":  "신청 기간",
  "method":  "신청 방법"
}}
"""
    try:
        res = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        data = json.loads(res.text)
        data["source_url"] = source_url
        return data
    except Exception as e:
        print(f"   ❌ [LLM 폴백 오류] {e}")
        return {"source_url": source_url}


# ──────────────────────────────────────────────────────────────────────────────
# CSS 셀렉터로 데이터 추출
# ──────────────────────────────────────────────────────────────────────────────
_FIELD_MAP = {
    "title_sel":   "title",
    "content_sel": "content",
    "target_sel":  "target",
    "period_sel":  "period",
    "method_sel":  "method",
}

async def extract_with_css(page: Page, rules: dict, source_url: str) -> dict:
    data: dict = {"source_url": source_url}
    for sel_key, field in _FIELD_MAP.items():
        sel = rules.get(sel_key, "")
        if not sel:
            data[field] = ""
            continue
        try:
            text = await page.locator(sel).first.inner_text(timeout=2_000)
            data[field] = text.strip()
        except:
            data[field] = ""
    return data


# ──────────────────────────────────────────────────────────────────────────────
# 정책 항목 링크 수집
# ──────────────────────────────────────────────────────────────────────────────
async def get_policy_links(page: Page) -> list[dict]:
    """사이드바·노이즈를 제외하고, 정책 항목으로 보이는 링크만 반환.
    대부분의 필터링은 JS(_GET_LINKS_JS) 내부에서 처리. Python 측은 추가 보호용."""
    raw: list[dict] = await page.evaluate(_GET_LINKS_JS)
    result = []
    for lk in raw:
        text = lk["text"]
        # Python 측 추가 필터 (JS에서 못 잡은 케이스 대비)
        if any(bad in text for bad in _IGNORE_TEXTS):
            continue
        result.append(lk)
    return result[:MAX_ITEMS_PER_PAGE]


# ──────────────────────────────────────────────────────────────────────────────
# '자세히보기' 버튼 수 카운트 (카드 목록 방식 감지용)
# ──────────────────────────────────────────────────────────────────────────────
async def count_detail_buttons(page: Page) -> int:
    """페이지에 표시된 '자세히보기'/'상세보기' 버튼의 수를 반환."""
    return await page.evaluate("""() => {
        const BTN_TEXTS = ['자세히보기', '상세보기', '자세히 보기', '상세 보기'];
        return Array.from(document.querySelectorAll('a')).filter(a => {
            const t = (a.innerText || '').trim();
            return BTN_TEXTS.includes(t) && a.offsetWidth > 0 && a.offsetHeight > 0;
        }).length;
    }""")


# ──────────────────────────────────────────────────────────────────────────────
# 페이지 유형 판별 (목록 vs 상세)
# ──────────────────────────────────────────────────────────────────────────────
async def is_detail_page(page: Page) -> bool:
    """
    페이지가 단일 정책 상세 페이지인지 판단.
    - 정책 링크가 2개 미만이거나 페이징이 없으면 상세 페이지로 간주.
    """
    links = await get_policy_links(page)
    has_paging: bool = await page.evaluate(
        "() => !!document.querySelector('.paging, .pagination, [class*=\"pag\"]')"
    )
    return len(links) < 3 or not has_paging


# ──────────────────────────────────────────────────────────────────────────────
# 탭 클릭 (action=click)
# ──────────────────────────────────────────────────────────────────────────────
async def click_tab(page: Page, target_text: str) -> bool:
    """target_text에 해당하는 탭/버튼을 클릭. 성공 시 True."""
    clean = re.sub(r"\(\d+.*?\)", "", target_text).strip()
    candidates = await page.locator(
        f"a:has-text('{clean}'), button:has-text('{clean}'), li:has-text('{clean}')"
    ).all()
    for el in candidates:
        if not await el.is_visible():
            continue
        # breadcrumb·헤더는 제외
        is_nav = await el.evaluate(
            "el => !!el.closest('.location, .breadcrumb, h1, h2, h3, header')"
        )
        if is_nav:
            continue
        try:
            async with page.expect_navigation(timeout=7_000):
                await el.click(force=True)
        except:
            await asyncio.sleep(2)
        print(f"   🔸 탭 클릭: [{clean}]")
        return True
    print(f"   ⚠️  탭 [{clean}] 를 찾지 못했습니다.")
    return False


# ──────────────────────────────────────────────────────────────────────────────
# 페이지 번호 이동
# ──────────────────────────────────────────────────────────────────────────────
async def go_to_page_num(page: Page, page_num: int) -> bool:
    """페이징 UI에서 page_num 번째 페이지로 이동. 성공 시 True."""
    if page_num <= 1:
        return True
    try:
        pager = page.locator(
            ".pagination a, .paging a, .page a, [class*='pag'] a"
        ).filter(has_text=str(page_num)).first
        if await pager.count() == 0:
            return False
        await pager.click(timeout=5_000)
        await asyncio.sleep(PAGE_WAIT)
        return True
    except:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# 상세 페이지 데이터 추출 (단건)
# ──────────────────────────────────────────────────────────────────────────────
async def extract_policy(
    page: Page,
    source_url: str,
    region: str,
    category: str,
    fallback_title: str = "",
    css_rules: dict | None = None,
) -> dict:
    domain = urlparse(source_url).netloc

    # CSS 규칙이 없으면 도메인 캐시 조회 or LLM 호출
    if css_rules is None:
        css_rules = await get_css_rules(page, domain)

    if css_rules:
        data = await extract_with_css(page, css_rules, source_url)
    else:
        data = await extract_with_llm(page, source_url)

    if not data.get("title") and fallback_title:
        data["title"] = fallback_title
    data.update({"region": region, "category": category})
    return data


# ──────────────────────────────────────────────────────────────────────────────
# 목록 페이지 순회 스크래핑
# ──────────────────────────────────────────────────────────────────────────────
async def scrape_list(
    page: Page,
    base_url: str,
    action: str,
    target_text: str | None,
    region: str,
    category: str,
) -> list[dict]:
    """
    목록 페이지를 페이징 순회하며 각 정책 상세 페이지 내용을 수집.

    최적화 전략:
      - 실제 URL(href)이 있는 항목 → 목록 재로드 없이 직접 이동 (빠름)
      - JS 링크(#none, javascript:) 항목 → 목록 복구 후 클릭
      - 도메인별 CSS 규칙을 첫 번째 상세 페이지에서만 LLM으로 추출하고 재사용
    """
    policies: list[dict] = []
    domain = urlparse(base_url).netloc
    css_rules: dict | None = None
    # 탭 클릭 이후 실제 목록 URL (AJAX 이후 바뀐 URL 캐싱)
    list_url_cache: str | None = None

    async def restore_list(pnum: int) -> None:
        """목록 상태 복구 + 페이지 이동."""
        nonlocal list_url_cache
        if list_url_cache and pnum == 1:
            await page.goto(list_url_cache, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        else:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await asyncio.sleep(PAGE_WAIT)
            if action == "click" and target_text:
                if await click_tab(page, target_text):
                    await asyncio.sleep(PAGE_WAIT)
                    list_url_cache = page.url  # 탭 클릭 후 URL 캐싱
        if pnum > 1:
            await go_to_page_num(page, pnum)

    for page_num in range(1, MAX_PAGES + 1):
        print(f"\n   📄 [페이지 {page_num}] 목록 로드 중...")
        await restore_list(page_num)

        links = await get_policy_links(page)
        if not links:
            print(f"   ✅ 항목 없음 — 종료 (마지막 페이지: {page_num - 1})")
            break

        print(f"   🔍 {len(links)}개 항목 발견")

        # 항목 방문 전략 결정
        # JS 링크가 하나라도 있으면 클릭 방식 사용 (목록 복구 필요)
        # - href="#none"  : 서울시 등 onclick 기반
        # - href="#"      : 강원도 등 AJAX 패널 기반  ← 이전에 누락
        # - href=""       : 빈 href (JS 이벤트만 사용)
        # - javascript:   : 명시적 JS 링크
        _JS_HREF = frozenset({"#", "#none", "", "javascript:void(0)", "javascript:;"})
        has_js = any(
            lk["raw_href"].strip().lower() in _JS_HREF
            or lk["raw_href"].lower().startswith("javascript")
            for lk in links
        )

        for idx, link in enumerate(links):
            text = link["text"]
            href = link["href"]
            raw  = link["raw_href"]
            print(f"      [{idx+1}/{len(links)}] {text[:25]}...")

            try:
                if not has_js and href and "javascript" not in raw:
                    # ── 직접 URL 이동 (빠름) ──
                    await page.goto(href, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                    await asyncio.sleep(1)
                else:
                    # ── 목록 복구 후 클릭 (JS 기반 사이트) ──
                    await restore_list(page_num)
                    before_url  = page.url
                    before_text = await page.evaluate(
                        "() => document.body.innerText.slice(0, 300)"
                    )
                    item_el = page.locator(f"a:has-text('{text}')").filter(visible=True).first
                    await item_el.click(timeout=5_000)
                    await asyncio.sleep(2.5)  # AJAX 로딩 대기
                    after_url  = page.url
                    after_text = await page.evaluate(
                        "() => document.body.innerText.slice(0, 300)"
                    )
                    # URL도 안 바뀌고 컨텐츠도 안 바뀌면 → 실제 이동 없음, 스킵
                    if after_url == before_url and after_text == before_text:
                        print(f"      ⏭️  URL/컨텐츠 미변경 — 스킵")
                        continue

                detail_url = page.url

                # CSS 규칙 초기화 (도메인별 최초 1회 LLM 호출)
                if css_rules is None:
                    css_rules = await get_css_rules(page, domain)

                data = await extract_policy(
                    page, detail_url, region, category,
                    fallback_title=text, css_rules=css_rules
                )

                # 데이터 유효성 검사 — 제목·내용이 모두 없으면 노이즈 페이지
                if not data.get("title") and not data.get("content"):
                    # CSS 규칙이 잘못된 페이지(목록 페이지 등)에서 추출된 것일 수 있음
                    # → 도메인 캐시를 무효화하여 다음 항목에서 재시도
                    async with _css_lock:
                        _css_cache.pop(domain, None)
                    css_rules = None
                    print(f"      ⚠️  빈 데이터 — CSS 캐시 무효화 후 스킵")
                    continue

                policies.append(data)
                print(f"      ✅ 수집: {data.get('title', text)[:30]}")

            except Exception as e:
                print(f"      ⚠️  실패: {text[:20]} — {e}")

        # 다음 페이지 존재 확인 (현재 목록 기준)
        # go_to_page_num이 False 를 반환하면 마지막 페이지
        await restore_list(page_num)
        if not await go_to_page_num(page, page_num + 1):
            print(f"   ✅ 마지막 페이지 ({page_num})")
            break

    return policies


# ──────────────────────────────────────────────────────────────────────────────
# '자세히보기' 버튼 기반 카드 목록 스크래핑 (URL 미변경 AJAX 방식)
# ──────────────────────────────────────────────────────────────────────────────
async def scrape_via_detail_buttons(
    page: Page,
    base_url: str,
    action: str,
    target_text: str | None,
    region: str,
    category: str,
) -> list[dict]:
    """
    '자세히보기'/'상세보기' 버튼이 있는 카드 목록 처리 (예: 강원도).
    URL이 변경되지 않는 AJAX 패널 방식 대응 — 인덱스 기반(.nth(i))으로 클릭.
    """
    policies: list[dict] = []
    domain = urlparse(base_url).netloc
    css_rules: dict | None = None
    list_url_cache: str | None = None

    async def restore_list(pnum: int) -> None:
        nonlocal list_url_cache
        if list_url_cache and pnum == 1:
            await page.goto(list_url_cache, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        else:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await asyncio.sleep(PAGE_WAIT)
            if action == "click" and target_text:
                if await click_tab(page, target_text):
                    await asyncio.sleep(PAGE_WAIT)
                    list_url_cache = page.url
        if pnum > 1:
            await go_to_page_num(page, pnum)

    for page_num in range(1, MAX_PAGES + 1):
        print(f"\n   📄 [페이지 {page_num}] 카드 목록 로드 (자세히보기 방식)...")
        await restore_list(page_num)

        # 현재 페이지의 카드 정보 수집 (제목 + 버튼 수)
        btn_infos: list[dict] = await page.evaluate(_GET_DETAIL_BTN_INFO_JS)
        count = len(btn_infos)
        if count == 0:
            print(f"   ✅ 버튼 없음 — 종료 (마지막 페이지: {page_num - 1})")
            break

        print(f"   🔍 {count}개 카드 발견")

        for i in range(min(count, MAX_ITEMS_PER_PAGE)):
            fallback_title = btn_infos[i].get("title", "") if i < len(btn_infos) else ""
            print(f"      [{i+1}/{count}] {fallback_title[:25] or '(제목 미확인)'}...")

            try:
                # 목록 상태 복구 후 i번째 버튼 클릭 (인덱스 기반)
                await restore_list(page_num)
                before_text = await page.evaluate(
                    "() => document.body.innerText.slice(0, 500)"
                )

                btn_locator = page.locator(
                    "a:has-text('자세히보기'), a:has-text('상세보기'), "
                    "a:has-text('자세히 보기'), a:has-text('상세 보기')"
                ).filter(visible=True).nth(i)

                await btn_locator.click(timeout=5_000)
                await asyncio.sleep(2.5)

                after_text = await page.evaluate(
                    "() => document.body.innerText.slice(0, 500)"
                )
                if after_text == before_text:
                    print(f"      ⏭️  컨텐츠 미변경 — 스킵")
                    continue

                detail_url = page.url  # URL이 안 바뀌어도 source_url로 기록

                if css_rules is None:
                    css_rules = await get_css_rules(page, domain)

                data = await extract_policy(
                    page, detail_url, region, category,
                    fallback_title=fallback_title,
                    css_rules=css_rules,
                )

                if not data.get("title") and not data.get("content"):
                    async with _css_lock:
                        _css_cache.pop(domain, None)
                    css_rules = None
                    print(f"      ⚠️  빈 데이터 — CSS 캐시 무효화 후 스킵")
                    continue

                policies.append(data)
                print(f"      ✅ 수집: {data.get('title', fallback_title)[:30]}")

            except Exception as e:
                print(f"      ⚠️  실패: {fallback_title[:20]} — {e}")

        # 다음 페이지 존재 확인 (URL 미변경 사이트도 내용은 바뀜)
        await restore_list(page_num)
        before_page = await page.evaluate("() => document.body.innerText.slice(0, 300)")
        moved = await go_to_page_num(page, page_num + 1)
        if not moved:
            print(f"   ✅ 마지막 페이지 ({page_num})")
            break
        after_page = await page.evaluate("() => document.body.innerText.slice(0, 300)")
        if after_page == before_page:
            print(f"   ✅ 페이지 이동 후 내용 미변경 — 마지막 페이지 ({page_num})")
            break

    return policies


# ──────────────────────────────────────────────────────────────────────────────
# target_info 단건 처리 (시나리오 분기)
# ──────────────────────────────────────────────────────────────────────────────
async def scrape_target(
    context: BrowserContext,
    region: str,
    target_info: dict,
) -> list[dict]:
    """
    target_boards.json의 target_data 항목 1개를 처리.
    시나리오 1·2·3 자동 분기.
    """
    page = await context.new_page()
    base_url    = target_info["url"]
    action      = target_info["action"]
    target_text = target_info.get("target_text")
    category    = target_info.get("category", "공통")
    policies: list[dict] = []

    try:
        await page.goto(base_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        await asyncio.sleep(PAGE_WAIT)

        # 탭 클릭 (action=click)
        if action == "click" and target_text:
            await click_tab(page, target_text)
            await asyncio.sleep(PAGE_WAIT)

        # ── 페이지 유형 자동 판별 ──
        # 1순위: '자세히보기'/'상세보기' 버튼이 2개 이상 → 카드 목록 방식
        detail_btn_count = await count_detail_buttons(page)
        if detail_btn_count >= 2:
            print(f"   🃏 [카드 목록 — 자세히보기 방식] {base_url} ({detail_btn_count}개 버튼)")
            policies = await scrape_via_detail_buttons(
                page, base_url, action, target_text, region, category
            )
        elif await is_detail_page(page):
            # 시나리오 3: URL 자체가 단일 정책 페이지
            print(f"   📌 [직접 추출] {base_url}")
            data = await extract_policy(page, page.url, region, category)
            if data:
                policies.append(data)
        else:
            # 시나리오 1·2: 목록 페이지 순회
            print(f"   📋 [목록 순회] {base_url}")
            policies = await scrape_list(
                page, base_url, action, target_text, region, category
            )

    except Exception as e:
        print(f"   ❌ [{region}/{category}] 오류: {e}")
    finally:
        await page.close()

    return policies


# ──────────────────────────────────────────────────────────────────────────────
# 지역 단위 처리
# ──────────────────────────────────────────────────────────────────────────────
async def scrape_region(browser, region_entry: dict) -> dict:
    region_name = region_entry["nm"]
    region_policies: list[dict] = []

    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
    )
    try:
        for target_info in region_entry.get("target_data", []):
            cat    = target_info.get("category", "공통")
            act    = target_info["action"]
            t_text = target_info.get("target_text", "")
            print(f" 👉 [{cat}] action={act} target_text={t_text or '-'}")
            polices = await scrape_target(context, region_name, target_info)
            region_policies.extend(polices)
    finally:
        await context.close()

    return {
        "region": region_name,
        "total":  len(region_policies),
        "policies": region_policies,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────────────────────
async def main() -> None:
    if not os.path.exists(INPUT_FILE):
        print(f"❌ {INPUT_FILE} 파일이 없습니다.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        targets: list[dict] = json.loads(f.read())

    # status=success 인 지역만 처리
    active = [t for t in targets if t.get("status") == "success"]
    print(f"🔥 총 {len(active)}개 지역 스크래핑 시작 (동시 처리: {REGION_CONCURRENCY})")
    print("-" * 60)

    all_data: list[dict] = []
    save_lock = asyncio.Lock()

    semaphore = asyncio.Semaphore(REGION_CONCURRENCY)

    async def process_with_sem(browser, entry: dict) -> None:
        async with semaphore:
            region_name = entry["nm"]
            print(f"\n🚀 [{region_name}] 시작")
            try:
                result = await scrape_region(browser, entry)
            except Exception as e:
                print(f"💥 [{region_name}] 치명 오류: {e}")
                result = {"region": region_name, "total": 0, "policies": []}

            async with save_lock:
                all_data.append(result)
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(all_data, f, ensure_ascii=False, indent=2)
            print(f" 💾 [{region_name}] {result['total']}개 저장 완료")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        await asyncio.gather(*[process_with_sem(browser, t) for t in active])
        await browser.close()

    print(f"\n✨ 완료! {OUTPUT_FILE} 확인 바람.")
    print(f"   총 수집 정책: {sum(r['total'] for r in all_data)}건")


if __name__ == "__main__":
    asyncio.run(main())
