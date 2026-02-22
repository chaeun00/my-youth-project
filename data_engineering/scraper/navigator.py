import json
import os
import asyncio
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from google import genai

# === 1. 설정 영역 ===
BASE_DIR = "data_engineering/scraper"
INPUT_FILE = os.path.join(BASE_DIR, "discovered_urls.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "target_boards.json")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

client = genai.Client(api_key=GOOGLE_API_KEY)
MAX_DEPTH = 4
MAX_RETRIES = 2 # 실패 시 재시도 횟수

async def get_page_summary(page):
    try:
        data = await page.evaluate('''() => {
            const hasTable = document.querySelector('table') ? "TABLE_PRESENT" : "NO_TABLE";
            const hasPaging = document.querySelector('.paging, .pagination, [class*="page"]') ? "PAGING_PRESENT" : "NO_PAGING";
            return `${hasTable} | ${hasPaging} | ${document.body.innerText.slice(0, 2000)}`;
        }''')
        return data.replace('\\n', ' ')
    except: return ""

async def extract_links_from_page(page, base_url):
    IGNORE_KEYWORDS = [
        "로그인", "회원가입", "이용약관", "개인정보", "페이스북", "인스타그램", "오시는길", 
        "청소년", "아동", "초등", "중등", "고등", "육아", "노인",
        "공지사항", "캘린더", "일정", "연구자료", "법령", "조례", "인사말", "조직도"
    ]
    TARGET_KEYWORDS = [
        "청년", "정책", "사업", "지원", "공고", "모집", "검색", "목록", 
        "일자리", "주거", "교육", "복지", "참여", "금융", "시", "자치구",
        "사회진입", "정착", "공통"
    ]
    try:
        links = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a')).map(a => ({
                text: a.innerText.trim(),
                href: a.href, 
                raw_href: a.getAttribute('href') || "", 
                onclick: a.getAttribute('onclick') || ""
            }));
        }''')
    except: return []
    
    filtered_links = []
    seen_urls = set()
    for link in links:
        text = link['text'].replace('\n', ' ')
        full_url = link['href']
        raw_href = link['raw_href']
        onclick = link['onclick']
        
        if not text or not full_url: continue
        is_js_link = "#none" in raw_href or raw_href.startswith("javascript")
        if is_js_link and not onclick: continue
        if full_url in seen_urls and not is_js_link: continue
        if any(bad in text for bad in IGNORE_KEYWORDS): continue
            
        if any(good in text for good in TARGET_KEYWORDS) or "바로가기" in text or "홈페이지" in text or "더보기" in text:
            filtered_links.append({"text": text, "url": full_url, "raw_href": raw_href, "onclick": onclick})
            seen_urls.add(full_url)
    return filtered_links[:50]

async def ask_gemini_for_navigation(region_name, current_url, links, page_text, depth):
    prompt = f"""
    너는 [{region_name}]의 청년 정책 데이터를 수집하는 '수석 데이터 엔지니어'이자 '지능형 네비게이터'야.
    목표: **[{region_name}] 관할(본청/자치구) 순수 청년 정책이 모두 나열된 [진짜 목록 페이지]**를 찾아, 스크래퍼가 클릭할 **'정확한 버튼 지령'**을 만드는 것.

    [현재 상황]: {region_name} | {current_url} | Depth: {depth}/{MAX_DEPTH}
    [페이지 요약]: {page_text}
    [링크 후보]: {json.dumps(links, ensure_ascii=False, indent=2)}

    =========================================
    [사냥 알고리즘: 시간순 진행]

    ▶ 1단계: 즉시 탈출 (FAIL) 검열
    현재 페이지가 아래 중 하나라면 즉시 FAIL하라.
    - 접근 불가: URL에 'error', '404' 포함
    - 대상 오류: '청소년', '중고생', '아동', '노인' 위주의 내용
    - 주체 오류: '중앙정부', '정부24', '전국공통', '타 지자체' 주도 정책
    - 노이즈: 인사말, 조직도, 법령/조례, 단순 공지사항(직원채용/입찰/행사 등)
      *(단, 노이즈 페이지라도 링크 후보 중에 '{region_name} 정책', '사업공고' 등 3단계 타겟 버튼이 있다면 FAIL 대신 NAVIGATE 할 것)*

    ▶ 2단계: 껍데기 돌파 (NAVIGATE)
    아직 '진짜 목록'이 아니다. 아래에 해당하면 상위/하위 메뉴나 '더보기' 버튼으로 NAVIGATE하라.
    - 인트로/허브: '정책정보 바로가기', '메인 입장' 또는 여러 카테고리를 안내하는 메뉴판 페이지
    - 맛보기/가짜 리스트: 요약에 정책이 몇 개 보이나 '더보기/전체보기' 버튼이 있는 경우, 혹은 **테이블(표)이나 페이징(1,2,3..) 버튼이 아예 없는 경우**
    - 상세 페이지: 특정 사업 하나(예: XX지원금 신청)만 길게 설명하는 경우

    ▶ 3단계: 목표물 포착 및 타겟팅 (버튼 사수)
    - **🔥통합 게시판 최우선 법칙**: '전체보기', '통합목록', '통합검색' 등 모든 정책이 한곳에 모인 게시판을 발견하면 1순위 타겟으로 삼아라. 개별 카테고리(일자리, 주거 등) 버튼들은 통합 게시판이 없을 때만 수집하라.
    - **키워드 우선순위**: '청년정책(Policy)', '검색/통합검색', '사업(Project)', '공고/모집' 위주로 탐색하라. ('지원(Support)', '정보(Info)', '전략'은 배제)
    - **동적 버튼 사수**: 주소가 `#none`이거나 현재와 같아도, 리스트를 부르는 **버튼의 정확한 텍스트(예: "XX시 정책(A건)")**를 찾아 타겟으로 잡아라.

    ▶ 4단계: 최종 판정 (FOUND_SINGLE / FOUND_MULTI)
    - **Priority 1 (FOUND_SINGLE)**: 1~2단계 결격 사유가 없는, 모든 장르를 포괄하는 통합 정책 검색/목록 페이지.
    - **Priority 2 (FOUND_MULTI)**: 통합 게시판을 도저히 찾을 수 없어 여러 카테고리를 뒤져야 하는 경우, 아래 버튼들의 텍스트를 빠짐없이 `endpoints`에 담아라.
      (1) '{region_name} 본청 정책'과 '시/군/구(자치구) 정책' 탭이 나뉘어 있는 경우.
      (2) '일자리', '주거', '교육', '복지', '사회진입기' 등 장르별 청년정책 카테고리 버튼들.
    =========================================

    JSON 응답 형식:
    {{
        "action": "FOUND_SINGLE" | "FOUND_MULTI" | "NAVIGATE" | "FAIL",
        "target_url": "이동할 URL 또는 클릭할 버튼 텍스트",
        "endpoints": ["클릭할 버튼 텍스트/URL 1", "..."],
        "reason": "1~4단계 알고리즘 논리적 판단 근거"
    }}
    """
    try:
        response = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt, config={'response_mime_type': 'application/json'})
        return json.loads(response.text)
    except Exception as e:
        print(f"   ❌ 제미나이 에러: {e}")
        return {"action": "FAIL"}

async def navigate_single_candidate(region_name, context, start_url):
    page = await context.new_page()
    current_url = start_url
    try:
        for depth in range(MAX_DEPTH + 1):
            print(f"   탐색 중... [Depth {depth}] {current_url}")
            if "error" in current_url.lower() or "404" in current_url: break

            try:
                await page.goto(current_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)
                current_url = page.url 
            except Exception:
                print(f"   ⚠️ 접속 지연 무시 (강행)")

            page_text = await get_page_summary(page)
            links = await extract_links_from_page(page, current_url)
            decision = await ask_gemini_for_navigation(region_name, current_url, links, page_text, depth)
            
            action = decision.get("action")
            target = decision.get("target_url")
            endpoints = decision.get("endpoints", [])
            reason = decision.get("reason")
            
            print(f"   🤖 제미나이: {action} -> {reason}")
            
            def build_instruction(hint):
                if not hint: return None
                if "http" in hint and "#none" not in hint and "javascript" not in hint:
                    category_name = hint.split('/')[-1] if '/' in hint else hint
                    return {"category": category_name, "url": hint, "action": "direct", "target_text": None}
                
                target_link = next((l for l in links if hint in l['text'] or l['text'] in hint), None)
                found_text = target_link['text'] if target_link else hint
                return {"category": found_text, "url": page.url, "action": "click", "target_text": found_text}

            async def move_to_target(url_or_text):
                if not url_or_text: return False
                if "http" in url_or_text and "#none" not in url_or_text:
                    try:
                        await page.goto(url_or_text, wait_until="domcontentloaded", timeout=20000)
                        return True
                    except: return False
                try:
                    target_link = next((l for l in links if l['text'] == url_or_text or url_or_text in l['text']), None)
                    if target_link:
                        await page.get_by_role("link", name=target_link['text']).first.click(timeout=5000)
                        return True
                except: return False
                return False

            if action == "FOUND_SINGLE":
                return {"type": "single", "target_data": [build_instruction(target or current_url)]}

            elif action == "FOUND_MULTI":
                instructions = [build_instruction(ep) for ep in endpoints if ep]
                return {"type": "multi", "target_data": [i for i in instructions if i]}
            
            elif action == "NAVIGATE":
                if not target:
                    for kw in ["더보기", "전체보기", "목록", "검색", "정책", region_name]:
                        fallback = next((l['text'] for l in links if kw in l['text']), None)
                        if fallback: target = fallback; break
                
                if target:
                    if await move_to_target(target):
                        await asyncio.sleep(2)
                        current_url = page.url
                        continue
                break
            elif action == "FAIL":
                return None
            else: break
    finally: await page.close()
    return None

async def select_the_best_one(region_name, findings):
    prompt = f"""
    [{region_name}]의 청년 정책을 수집하기에 가장 완벽한 곳을 골라줘.
    [후보 목록]: {json.dumps(findings, ensure_ascii=False, indent=2)}
    [🏆 우승 기준]: 1. 지역 브랜딩 우선, 2. 멀티/통합검색 우선, 3. 청소년 사이트 탈락.
    JSON 응답: {{ "best_index": 0 또는 1 }}
    """
    try:
        res = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt, config={'response_mime_type': 'application/json'})
        return findings[json.loads(res.text)['best_index']]
    except:
        return next((f for f in findings if f['type'] == 'multi'), findings[0])

# [🔥 신규 기능] 기존 타겟 URL이 유효한지 검증하는 함수
async def verify_urls_accessibility(context, target_data):
    if not target_data: return False
    urls_to_check = set([item['url'] for item in target_data if item.get('url')])
    if not urls_to_check: return False

    page = await context.new_page()
    try:
        for url in urls_to_check:
            try:
                # 15초 안에 HTTP 200~399 응답이 오는지 확인
                response = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                if not response or response.status >= 400:
                    print(f"   ⚠️ 검증 실패 (HTTP {response.status if response else 'Timeout'}): {url}")
                    return False
            except Exception as e:
                print(f"   ⚠️ 검증 실패 (접속 에러): {url}")
                return False
        return True # 모든 URL이 정상 작동함
    finally:
        await page.close()

async def process_region_with_retries(target, existing_data_map, browser):
    region_name = target['nm']
    candidate_urls = target.get('urls', [])
    
    # 스텔스 모드 컨텍스트 생성 (재시도 시마다 깨끗한 환경)
    context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36")
    
    try:
        # 1. 기존 데이터 유효성 검증 (Pre-check)
        existing_info = existing_data_map.get(region_name)
        if existing_info and existing_info.get("status") == "success":
            print(f" 🔍 기존 [{region_name}] 데이터 발견. URL 생존 여부를 검증합니다...")
            is_valid = await verify_urls_accessibility(context, existing_info.get("target_data", []))
            
            if is_valid:
                print(f" 🟢 [검증 통과] 기존 URL이 유효합니다. 탐색을 건너뜁니다.")
                return existing_info
            else:
                print(f" 🔴 [검증 실패] URL이 죽었거나 접속 불가합니다. 재탐색을 시작합니다.")

        # 2. 신규 탐색 또는 재탐색 시작 (Retry Loop)
        for attempt in range(MAX_RETRIES):
            print(f"\n🚀 [{region_name}] 정밀 탐색 시작 (시도: {attempt + 1}/{MAX_RETRIES})")
            findings = [] 
            
            for url in candidate_urls:
                print(f" 👉 진입: {url}")
                result = await navigate_single_candidate(region_name, context, url)
                if result: findings.append(result)
                
            if findings:
                winner = await select_the_best_one(region_name, findings) if len(findings) > 1 else findings[0]
                print(f" 🏆 최종 우승 선정 완료")
                return {
                    "nm": region_name,
                    "type": winner['type'],
                    "target_data": winner.get('target_data', []),
                    "status": "success"
                }
            else:
                print(f" ⚠️ [{region_name}] 탐색 실패. 유효한 리스트를 찾지 못했습니다.")
                if attempt < MAX_RETRIES - 1:
                    print(" 🔄 처음부터 다시 시도합니다...")
                    await asyncio.sleep(3)
                    
        return {"nm": region_name, "status": "failed"}
    finally:
        await context.close()

async def main():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ {INPUT_FILE} 파일이 없습니다.")
        return

    targets = []
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content: targets = json.loads(content)
    except:
        print(f"💥 {INPUT_FILE} 로딩 오류.")
        return

    if not targets:
        print("🚩 처리할 타겟이 없습니다.")
        return

    # [🔥 핵심] 기존에 저장된 target_boards.json 맵핑 데이터 불러오기
    existing_data_map = {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    existing_list = json.loads(content)
                    for item in existing_list:
                        existing_data_map[item['nm']] = item
        except: pass # 파싱 실패 시 무시하고 새로 생성

    print(f"🔥 총 {len(targets)}개 지역에 대한 '지능형 하이브리드 탐색'을 시작합니다.")
    print("-" * 50)
    
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        
        for i, target in enumerate(targets):
            region_name = target['nm']
            print(f"\n[{i+1}/{len(targets)}] 🚩 {region_name} 처리 중")
            try:
                result = await process_region_with_retries(target, existing_data_map, browser)
                results.append(result)
                
                # 중간 저장 (Incremental Save)
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                print(f"   💾 {region_name} 저장 완료")
            except Exception as e: print(f"   💥 오류: {e}")
            
        await browser.close()
    print(f"\n✨ 사냥 종료! {OUTPUT_FILE} 확인 바람.")

if __name__ == "__main__":
    asyncio.run(main())