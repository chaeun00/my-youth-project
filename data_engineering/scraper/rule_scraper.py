import json
import os
import asyncio
import re
from playwright.async_api import async_playwright
from google import genai

# === 1. 설정 영역 ===
BASE_DIR = "data_engineering/scraper"
INPUT_FILE = os.path.join(BASE_DIR, "target_boards.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "scraped_policies_fast.json")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

client = genai.Client(api_key=GOOGLE_API_KEY)
category_session_urls = {}

MAX_ITEMS_PER_PAGE = 3
MAX_PAGES = 2

async def extract_css_rules_with_gemini(page):
    print("      🧠 [AI 작동] 첫 페이지 HTML 분석 및 CSS 규칙 추출 중...")
    html_content = await page.evaluate('''() => {
        let clone = document.body.cloneNode(true);
        clone.querySelectorAll('script, style, svg, img, nav, footer, header').forEach(e => e.remove());
        return clone.innerHTML.substring(0, 8000); 
    }''')
    prompt = f"""
    너는 웹 스크래핑 전문가야. 아래 청년 정책 상세 페이지의 HTML을 분석해서, 핵심 정보를 추출할 수 있는 **Playwright용 CSS Selector**를 만들어줘.
    [HTML 원본]: {html_content}
    [응답 JSON 규칙]:
    {{
        "title_selector": "정책 제목 추출 CSS",
        "content_selector": "지원 내용 추출 CSS",
        "target_selector": "지원 대상 추출 CSS",
        "period_selector": "신청 기간 추출 CSS"
    }}
    """
    try:
        res = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt, config={'response_mime_type': 'application/json'})
        rules = json.loads(res.text)
        print(f"      🎯 [규칙 획득] {rules}")
        return rules
    except: return None

async def parse_data_with_rules(page, rules, url):
    data = {"source_url": url, "is_valid": True}
    for key, selector in rules.items():
        try:
            text = await page.locator(selector).first.inner_text(timeout=2000)
            data[key.replace('_selector', '')] = text.strip()
        except: data[key.replace('_selector', '')] = "추출 불가 (구조 불일치)"
    return data

async def restore_list_state(page, base_url, action, target_text, page_num):
    clean_target = re.sub(r'\(\d+.*?\)', '', target_text).strip() if target_text else ""
    target_url = category_session_urls.get(clean_target)
    
    # 1. 캐시된 '진짜 주소(ctList.do 등)'가 있으면 텔레포트
    if target_url:
        if page.url != target_url:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
    else:
        # 2. 없으면 기본 주소로 가서 탭을 '물리적으로' 클릭함
        await page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
        if action == "click" and clean_target:
            # [🔥 핵심 수술 1] 빵부스러기(Breadcrumb)를 피하고 진짜 탭만 찾는 로직
            elements = await page.locator(f"a:has-text('{clean_target}'), li:has-text('{clean_target}')").all()
            for el in elements:
                if await el.is_visible():
                    # 상위 요소 중에 location, breadcrumb, h1~h3가 있으면 껍데기로 간주하고 버림
                    is_trash = await el.evaluate("el => !!el.closest('.location, .breadcrumb, h1, h2, h3')")
                    if not is_trash:
                        print(f"      🔸 [JS 이벤트 타격] '{clean_target}' 탭 클릭 (onclick 발동)")
                        try:
                            # Playwright native click이 href="#none"과 onclick 이벤트를 완벽히 실행함
                            async with page.expect_navigation(timeout=7000):
                                await el.click(force=True)
                        except:
                            await asyncio.sleep(3) # AJAX로만 변경될 경우를 대비한 대기
                        
                        # 바뀐 주소를 영구 저장
                        category_session_urls[clean_target] = page.url
                        print(f"      🎯 https://m.kpedia.jp/w/7001 {page.url}")
                        break

    # 3. 페이징 처리
    if page_num > 1:
        try:
            page_link = page.locator(f".pagination a, .paging a, .page a").filter(has_text=str(page_num)).first
            await page_link.click(timeout=5000)
            await asyncio.sleep(2)
        except: pass

    return clean_target

async def scrape_target(context, region_name, target_info):
    page = await context.new_page()
    base_url = target_info['url']
    action = target_info['action']
    target_text = target_info.get('target_text')
    category = target_info.get('category', '공통')
    
    policies = []
    css_rules = None 

    try:
        for current_page in range(1, MAX_PAGES + 1):
            print(f"\n   🔵 --- [페이지 {current_page} 진입 준비] ---")
            
            clean_target = await restore_list_state(page, base_url, action, target_text, current_page)
            
            # [🔥 핵심 수술 2] 사이드바 메뉴(중앙정부/타지역 정책 등) 원천 차단
            valid_titles = await page.evaluate('''([cleanTarget]) => {
                // aside, snb 등 사이드바를 감지하는 국룰 클래스 모조리 블랙리스트화
                const badSelectors = '.aside, aside, #aside, .snb, .lnb, .location, .breadcrumb, header, footer';
                const links = Array.from(document.querySelectorAll('a'));
                
                return links.filter(a => {
                    if (a.closest(badSelectors)) return false; // 사이드바 탈락
                    if (a.offsetWidth === 0 || a.offsetHeight === 0) return false; // 숨김 요소 탈락
                    
                    const t = a.innerText.replace(/\\n/g, ' ').trim();
                    if (t.length < 5) return false;
                    if (t.match(/\\(\\d+[건개]\\)$/)) return false; // 탭 이름 탈락
                    if (cleanTarget && t === cleanTarget) return false;
                    
                    // 서울시 특유의 사이드바 쓰레기 텍스트 강제 차단
                    if (t.includes('중앙정부') || t.includes('타지역') || t.includes('캘린더')) return false;

                    return true;
                }).map(a => a.innerText.replace(/\\n/g, ' ').trim())
                  .filter((t, i, s) => s.indexOf(t) === i); // 중복 제거
            }''', [clean_target])
            
            if not valid_titles: break

            limit = min(len(valid_titles), MAX_ITEMS_PER_PAGE)
            for i in range(limit):
                target_title = valid_titles[i]
                try:
                    print(f"      🔸 클릭: [{target_title[:15]}...]")
                    
                    # [🔥 핵심 수술 3] 사령관님 스크린샷의 onclick을 완벽히 실행시키는 물리 타격
                    target_link = page.locator(f"a:has-text('{target_title}')").filter(visible=True).first
                    await target_link.click(timeout=5000)
                    await asyncio.sleep(2) 
                    
                    if not css_rules: css_rules = await extract_css_rules_with_gemini(page)
                    if css_rules:
                        data = await parse_data_with_rules(page, css_rules, page.url)
                        data['category'] = category
                        if 'title' not in data or data['title'] == '추출 불가':
                            data['title'] = target_title 
                        policies.append(data)
                        print(f"      ⚡ [{i+1}/{limit}] 추출 성공: {target_title[:10]}...")
                except Exception as e: 
                    print(f"      ⚠️ 항목 클릭 실패: {target_title[:10]}")
                finally:
                    # 완벽하게 캐시된 카테고리 URL로 상태 복귀
                    await restore_list_state(page, base_url, action, target_text, current_page)

    except Exception as e:
        print(f"   ❌ 오류: {e}")
    finally:
        await page.close()
    
    return policies

async def main():
    if not os.path.exists(INPUT_FILE): return
    with open(INPUT_FILE, "r", encoding="utf-8") as f: targets = json.loads(f.read())

    all_data = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})

        for region in targets:
            if region.get('status') != 'success': continue
            region_name = region['nm']
            print(f"\n🚀 [{region_name}] AI 룰 기반 스크래핑 시작")
            
            region_policies = []
            for target_info in region.get('target_data', []):
                print(f" 👉 탭/카테고리 진입: {target_info.get('category')} | Action: {target_info['action']}")
                policies = await scrape_target(context, region_name, target_info)
                region_policies.extend(policies)
            
            all_data.append({"region": region_name, "total": len(region_policies), "policies": region_policies})
            
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(all_data, f, ensure_ascii=False, indent=2)

        await browser.close()
    print(f"\n✨ 수집 종료! {OUTPUT_FILE}을 확인하세요.")

if __name__ == "__main__":
    asyncio.run(main())