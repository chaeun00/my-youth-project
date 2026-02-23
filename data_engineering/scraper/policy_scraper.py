#!/usr/bin/env python3
"""
청년 정책 스크래퍼 (최적화 + 상세 데이터 정밀 추출 버전)
=====================================
입력 : data_engineering/scraper/target_boards.json
출력 : data_engineering/scraper/scraped_policies.json
"""

import json
import os
import asyncio
import re
from urllib.parse import urlparse
from playwright.async_api import async_playwright, Page, BrowserContext
from google import genai

# ── 설정 ──────────────────────────────────────────────────────────────────────
BASE_DIR   = "data_engineering/scraper"
INPUT_FILE = os.path.join(BASE_DIR, "target_boards.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "scraped_policies.json")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=GOOGLE_API_KEY)

MAX_PAGES          = 100   # 페이지 최대 순회 수
MAX_ITEMS_PER_PAGE = 100   # 페이지당 최대 항목 수
REGION_CONCURRENCY = 2    # 동시 처리 지역 수
PAGE_WAIT          = 1.5  # 페이지 로드 후 대기(초)
NAV_TIMEOUT        = 20_000  # 페이지 이동 타임아웃(ms)

# ── 공통 JS 스니펫 ─────────────────────────────────────────────────────────────

_TRIM_HTML_JS = """() => {
    const mainSelectors = ['.detailview', '.board_view', '#content', 'main', '.content', '#main', '.skinContainer'];
    let targetEl = document.body;
    for (let sel of mainSelectors) {
        const el = document.querySelector(sel);
        if (el) { targetEl = el; break; }
    }

    let c = targetEl.cloneNode(true);
    c.querySelectorAll(
        'script,style,svg,img,nav,footer,header,aside,.aside,' +
        '.gnb,.lnb,.snb,.location,.breadcrumb,.sitemap,.skip,.skip-nav'
    ).forEach(e => e.remove());
    
    return c.innerHTML.substring(0, 15000);
}"""

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
        /^https?:\\/\\//,    
        / > /,              
    ];
    const IGNORE_EXACT = new Set([
        '신청하기','자세히보기','더보기','전체보기','목록','목록으로',
        '이전','다음','처음','끝','HOME','home','TOP','top',
        '비교하기','정책비교','관심','공유','인쇄','스크랩',
        '신청','접수','확인','닫기',
    ]);

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

_GET_DETAIL_BTN_INFO_JS = """() => {
    const BTN_TEXTS = ['자세히보기', '상세보기', '자세히 보기', '상세 보기'];
    const btns = Array.from(document.querySelectorAll('a')).filter(a => {
        const t = (a.innerText || '').trim();
        return BTN_TEXTS.includes(t) && a.offsetWidth > 0 && a.offsetHeight > 0;
    });
    return btns.map(btn => {
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
                const texts = Array.from(card.querySelectorAll('*'))
                    .map(el => (el.innerText || '').trim())
                    .filter(t => t.length >= 5 && t !== btn.innerText.trim());
                title = texts.sort((a, b) => b.length - a.length)[0] || '';
            }
        }
        return { title: title.substring(0, 100) };
    });
}"""

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
    async with _css_lock:
        if domain in _css_cache:
            return _css_cache[domain]

    print(f"   🧠 [LLM] {domain} CSS 셀렉터 추출 (최초 1회)")
    html = await page.evaluate(_TRIM_HTML_JS)

    # [🔥 핵심 수술 1] 프롬프트 개조: AI가 범용적으로 정확한 의미를 파악하여 CSS를 짜도록 지시
    prompt = f"""아래는 청년 정책 **상세 페이지** HTML이야.
다음 6개 항목을 각각 추출할 수 있는 Playwright CSS Selector를 JSON으로 줘.

[🔥 중요 작성 가이드 - 전국 지자체 공통 적용]
1. target_sel(신청자격): 연령 한 줄만 가져오지 마라! '거주지', '소득', '학력' 등 전체 신청자격 내용이 모두 포함된 부모 태그(tbody, ul, div 전체)를 지정해라!
2. period_sel(신청기간): 반드시 '사업 신청 기간' 또는 '모집 기간'을 가져와라. '사업 운영 기간'을 지정하면 절대 안된다!
3. method_sel(신청방법): 표 안에 '신청 사이트(URL)'가 있다면 그 영역을 최우선으로 포함하고, 없으면 '신청 절차'가 담긴 영역을 지정해라.
4. category_sel(정책분야): 페이지 내에 '정책분야', '분야' (예: 복지·문화, 일자리 등)가 명시되어 있다면 그 텍스트를 추출할 셀렉터를 지정해라.
5. 표(Table/Div) 형태 데이터는 무조건 `:has()`와 `:has-text()`를 조합해서 정확히 타겟팅해라! (예: `tr:has(th:has-text('신청기간')) td`)

HTML:
{html}

응답 JSON:
{{
  "category_sel": "정책분야 CSS 셀렉터 (화면에 명시되어 있을 경우 지정, 없으면 빈 문자열)",
  "title_sel": "정책 제목 CSS 셀렉터",
  "content_sel": "지원 내용 및 지원 금액 CSS 셀렉터 (금액 정보가 포함된 영역 지정)",
  "target_sel": "지원 대상 및 신청 자격 '전체' 정보가 포함된 부모 CSS 셀렉터",
  "period_sel": "'사업 신청 기간' CSS 셀렉터 (운영 기간 X)",
  "method_sel": "신청 사이트 또는 신청 방법/절차 CSS 셀렉터"
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
        rules = {k: v for k, v in rules.items() if v}
        print(f"   🎯 [셀렉터 획득] {rules}")
    except Exception as e:
        print(f"   ❌ [LLM 오류] {e}")
        rules = None

    async with _css_lock:
        _css_cache[domain] = rules
    return rules


async def extract_with_llm(page: Page, source_url: str) -> dict:
    print(f"   🧠 [LLM 폴백] 전체 내용 직접 파싱")
    html = await page.evaluate(_TRIM_HTML_JS)
    
    # [🔥 핵심 수술 2] 폴백 LLM도 똑같은 기준을 적용받도록 강제
    prompt = f"""아래는 청년 정책 상세 페이지 HTML이야.
다음 항목을 추출해서 JSON으로 줘. 항목이 없으면 빈 문자열로 줘.

[🔥 중요 지침]
- target: 연령뿐만 아니라 거주지, 소득요건, 우대사항 등 신청자격 '전체' 내용을 요약해.
- period: '사업 운영 기간'이 아닌 '사업 신청 기간'을 정확히 추출해!
- method: 신청 사이트 주소가 있으면 반드시 포함하고, 신청 절차도 적어줘.
- category: 페이지 내에 명시된 정책분야(예: 일자리, 주거, 복지·문화 등)를 추출해. 없으면 비워둬.

HTML:
{html}

응답 JSON:
{{
  "category": "정책분야",
  "title": "정책 제목",
  "content": "지원 내용 및 지원 금액",
  "target": "지원 대상 및 신청 자격 상세 전체",
  "period": "사업 신청 기간",
  "method": "신청 사이트 및 신청 방법"
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


# [🔥 핵심 수술 3] 카테고리 셀렉터 필드 맵핑 추가
_FIELD_MAP = {
    "category_sel": "category",
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
            clean_text = re.sub(r'\s+', ' ', text).strip()
            data[field] = clean_text
        except:
            data[field] = ""
    return data


async def get_policy_links(page: Page) -> list[dict]:
    raw: list[dict] = await page.evaluate(_GET_LINKS_JS)
    result = []
    for lk in raw:
        text = lk["text"]
        if any(bad in text for bad in _IGNORE_TEXTS):
            continue
        result.append(lk)
    return result[:MAX_ITEMS_PER_PAGE]


async def count_detail_buttons(page: Page) -> int:
    return await page.evaluate("""() => {
        const BTN_TEXTS = ['자세히보기', '상세보기', '자세히 보기', '상세 보기'];
        return Array.from(document.querySelectorAll('a')).filter(a => {
            const t = (a.innerText || '').trim();
            return BTN_TEXTS.includes(t) && a.offsetWidth > 0 && a.offsetHeight > 0;
        }).length;
    }""")


async def is_detail_page(page: Page) -> bool:
    links = await get_policy_links(page)
    has_paging: bool = await page.evaluate(
        "() => !!document.querySelector('.paging, .pagination, [class*=\"pag\"]')"
    )
    return len(links) < 3 or not has_paging


async def click_tab(page: Page, target_text: str) -> bool:
    clean = re.sub(r"\(\d+.*?\)", "", target_text).strip()
    candidates = await page.locator(
        f"a:has-text('{clean}'), button:has-text('{clean}'), li:has-text('{clean}')"
    ).all()
    for el in candidates:
        if not await el.is_visible():
            continue
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


async def go_to_page_num(page: Page, page_num: int) -> bool:
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


async def extract_policy(
    page: Page,
    source_url: str,
    region: str,
    json_category: str,
    fallback_title: str = "",
    css_rules: dict | None = None,
) -> dict:
    domain = urlparse(source_url).netloc

    if css_rules is None:
        css_rules = await get_css_rules(page, domain)

    if css_rules:
        data = await extract_with_css(page, css_rules, source_url)
        empty_count = sum(1 for v in [data.get('target'), data.get('period'), data.get('method')] if not v)
        if empty_count >= 2:
            print(f"   ⚠️ CSS 추출 결과 부실. LLM 딥다이브 파싱으로 전환합니다.")
            data = await extract_with_llm(page, source_url)
    else:
        data = await extract_with_llm(page, source_url)

    if not data.get("title") and fallback_title:
        data["title"] = fallback_title
        
    # [🔥 핵심 수술 4] 웹페이지에서 직접 뽑은 카테고리(정책분야)가 있으면 최우선 적용!
    # 없다면 target_boards.json에서 넘겨준 카테고리(json_category)를 폴백으로 사용
    extracted_cat = data.get("category", "").strip()
    if not extracted_cat or "추출 불가" in extracted_cat:
        data["category"] = json_category 
        
    data["region"] = region
    return data


async def scrape_list(
    page: Page,
    base_url: str,
    action: str,
    target_text: str | None,
    region: str,
    category: str,
) -> list[dict]:
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
        print(f"\n   📄 [페이지 {page_num}] 목록 로드 중...")
        await restore_list(page_num)

        links = await get_policy_links(page)
        if not links:
            print(f"   ✅ 항목 없음 — 종료 (마지막 페이지: {page_num - 1})")
            break

        print(f"   🔍 {len(links)}개 항목 발견")

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
                    await page.goto(href, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                    await asyncio.sleep(1)
                else:
                    await restore_list(page_num)
                    before_url  = page.url
                    before_text = await page.evaluate("() => document.body.innerText.slice(0, 300)")
                    item_el = page.locator(f"a:has-text('{text}')").filter(visible=True).first
                    await item_el.click(timeout=5_000)
                    await asyncio.sleep(2.5)
                    after_url  = page.url
                    after_text = await page.evaluate("() => document.body.innerText.slice(0, 300)")
                    if after_url == before_url and after_text == before_text:
                        print(f"      ⏭️  URL/컨텐츠 미변경 — 스킵")
                        continue

                detail_url = page.url

                if css_rules is None:
                    css_rules = await get_css_rules(page, domain)

                data = await extract_policy(
                    page, detail_url, region, category,
                    fallback_title=text, css_rules=css_rules
                )

                if not data.get("title") and not data.get("content"):
                    async with _css_lock:
                        _css_cache.pop(domain, None)
                    css_rules = None
                    print(f"      ⚠️  빈 데이터 — CSS 캐시 무효화 후 스킵")
                    continue

                policies.append(data)
                print(f"      ✅ 수집: {data.get('title', text)[:30]}")

            except Exception as e:
                print(f"      ⚠️  실패: {text[:20]} — {e}")

        await restore_list(page_num)
        if not await go_to_page_num(page, page_num + 1):
            print(f"   ✅ 마지막 페이지 ({page_num})")
            break

    return policies


async def scrape_via_detail_buttons(
    page: Page,
    base_url: str,
    action: str,
    target_text: str | None,
    region: str,
    category: str,
) -> list[dict]:
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
                await restore_list(page_num)
                before_text = await page.evaluate("() => document.body.innerText.slice(0, 500)")

                btn_locator = page.locator(
                    "a:has-text('자세히보기'), a:has-text('상세보기'), "
                    "a:has-text('자세히 보기'), a:has-text('상세 보기')"
                ).filter(visible=True).nth(i)

                await btn_locator.click(timeout=5_000)
                await asyncio.sleep(2.5)

                after_text = await page.evaluate("() => document.body.innerText.slice(0, 500)")
                if after_text == before_text:
                    print(f"      ⏭️  컨텐츠 미변경 — 스킵")
                    continue

                detail_url = page.url

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


async def scrape_target(
    context: BrowserContext,
    region: str,
    target_info: dict,
) -> list[dict]:
    page = await context.new_page()
    base_url    = target_info["url"]
    action      = target_info["action"]
    target_text = target_info.get("target_text")
    category    = target_info.get("category", "공통")
    policies: list[dict] = []

    try:
        await page.goto(base_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        await asyncio.sleep(PAGE_WAIT)

        if action == "click" and target_text:
            await click_tab(page, target_text)
            await asyncio.sleep(PAGE_WAIT)

        detail_btn_count = await count_detail_buttons(page)
        if detail_btn_count >= 2:
            print(f"   🃏 [카드 목록 — 자세히보기 방식] {base_url} ({detail_btn_count}개 버튼)")
            policies = await scrape_via_detail_buttons(page, base_url, action, target_text, region, category)
        elif await is_detail_page(page):
            print(f"   📌 [직접 추출] {base_url}")
            data = await extract_policy(page, page.url, region, category)
            if data:
                policies.append(data)
        else:
            print(f"   📋 [목록 순회] {base_url}")
            policies = await scrape_list(page, base_url, action, target_text, region, category)

    except Exception as e:
        print(f"   ❌ [{region}/{category}] 오류: {e}")
    finally:
        await page.close()

    return policies


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


async def main() -> None:
    if not os.path.exists(INPUT_FILE):
        print(f"❌ {INPUT_FILE} 파일이 없습니다.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        targets: list[dict] = json.loads(f.read())

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