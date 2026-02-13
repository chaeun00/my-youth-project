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

# 제미나이 클라이언트 생성
client = genai.Client(api_key=GOOGLE_API_KEY)

MAX_DEPTH = 4

async def get_page_summary(page):
    """
    페이지의 상단 텍스트 2000자만 추출합니다.
    이유: 제미나이에게 '이 페이지가 공지사항인지, 정책 목록인지' 판단할 컨텍스트를 제공하면서도
    토큰 비용을 아끼기 위함입니다.
    """
    try:
        text = await page.evaluate("document.body.innerText")
        cleaned_text = ' '.join(text.split())
        return cleaned_text[:2000]
    except Exception:
        return ""

async def extract_links_from_page(page, base_url):
    """링크 추출 및 1차 키워드 필터링"""
    IGNORE_KEYWORDS = [
        "로그인", "회원가입", "이용약관", "개인정보", "페이스북", "인스타그램", "오시는길", 
        "청소년", "아동", "초등", "중등", "고등", "육아", "노인",
        "공지사항", "캘린더", "일정", "연구자료", "법령", "조례", "인사말", "조직도"
    ]
    TARGET_KEYWORDS = [
        "청년", "정책", "사업", "지원", "공고", "모집", "검색", "목록", 
        "일자리", "주거", "교육", "복지", "참여", "금융", "시", "자치구" 
    ]
    
    try:
        links = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a')).map(a => ({
                text: a.innerText.trim(),
                href: a.href, // 브라우저가 완성한 Full URL
                raw_href: a.getAttribute('href') || "", // 비교용 원본
                onclick: a.getAttribute('onclick') || ""
            }));
        }''')
    except:
        return []
    
    filtered_links = []
    seen_urls = set()

    for link in links:
        text = link['text'].replace('\n', ' ')
        full_url = link['href']
        raw_href = link['raw_href']
        onclick = link['onclick']
        
        if not text or not full_url: continue
        
        is_js_link = "#none" in raw_href or raw_href.startswith("javascript")
        
        if is_js_link and not onclick:
            continue
            
        if full_url in seen_urls and not is_js_link: continue
        
        if any(bad in text for bad in IGNORE_KEYWORDS):
            continue
            
        if any(good in text for good in TARGET_KEYWORDS) or "바로가기" in text or "홈페이지" in text or "더보기" in text:
            filtered_links.append({"text": text, "url": full_url, "onclick": onclick})
            seen_urls.add(full_url)
            
    return filtered_links[:50]

async def ask_gemini_for_navigation(region_name, current_url, links, page_text, depth):
    prompt = f"""
    너는 [{region_name}]의 청년 정책 데이터를 수집하는 '수석 데이터 엔지니어'야.
    목표: [{region_name}]의 **실질적인 정책 공고 리스트**가 담긴 페이지를 찾는 것.

    [현재 상황]:
    - 지역: {region_name} | URL: {current_url} | 탐색 깊이: {depth}/{MAX_DEPTH}
    - **주의**: 현재 URL에 'error'나 '404'가 포함되어 있다면, 잘못된 경로로 온 것이니 FAIL을 선언하고 이전으로 돌아가야 해.

    [페이지 요약 (상단 2000자)]:
    {page_text}

    [발견된 링크 후보]:
    {json.dumps(links, ensure_ascii=False, indent=2)}

    [🚨 인트로(Intro) 페이지 돌파]:
    - 만약 페이지에 '정책정보 바로가기', '메인으로 이동', '입장하기' 같은 버튼이 있다면 고민하지 말고 NAVIGATE해. 이것은 대문일 뿐이야.
    - **중요**: <a href="#none"> 이거나 주소가 없어도 `onclick` 이벤트가 있고 텍스트가 '{region_name} 정책' 혹은 '자치구 정책'처럼 명확하다면, 해당 링크를 통해 리스트가 전환됨을 인지하고 해당 URL(혹은 텍스트)을 선택해.

    [🚫 즉시 실패(FAIL) 기준]:
    1. **타겟 오류**: '청소년', '아동', '노인' 대상 사이트.
    2. **정보 부족**: 단순 '인사말', '조직도', '법령', '캘린더', '연구자료' 페이지.
    3. **단순 공지사항**: 제목이 '공지사항'이고 내용이 채용/행사/입찰 위주라면 과감히 버려.
    4. **상세 페이지 함정**: 특정 사업 하나만 설명하는 페이지는 목록이 아님.

    [🚨 핵심 사냥/필터링 규칙 - 지역 순혈주의]:
    1. **타지역/중앙정부 배제**: 만약 현재 [페이지 요약] 내용이 '중앙정부 정책', '타지역 정책', '전국 정책'을 다루고 있다면 그 페이지는 버려.
    2. **지역명 일치 필수**: '청년정책'보다 **'{region_name} 정책'**, **'{region_name} 청년 사업'**, **'{region_name} 청년정책 검색'**처럼 지역명이 명시된 링크를 최우선(High Priority)으로 선택해.
    3. **예외 허용**: 단, 사이트 이름 자체가 '정부24'나 '온통청년(중앙정부 포털)'인 경우는 중앙정부 정책이 맞으므로 예외로 둠. 하지만 지금은 지자체 사이트를 털고 있으므로 지역 정책이 우선임.
    4. **탭/섹션 분리 및 건수(Count) 감지**: 
       - 페이지 내에 '{region_name} 정책(000건)'과 '자치구 정책(000건)'처럼 **정책 건수가 나뉘어 표시**되어 있다면, 이것은 반드시 분리해서 수집해야 할 멀티 엔드포인트야.
       - 각 섹션 제목 옆에 있는 **'더보기 >'** 링크나 해당 탭을 클릭했을 때의 URL을 각각 찾아 **FOUND_MULTI**로 응답해.

    [🎯 성공(FOUND) 기준]:
    - **Priority 1 (FOUND_MULTI)**: 한 페이지에 '{region_name} 정책'과 '타기관/자치구 정책'이 나뉘어 있고, 각각의 리스트로 가는 링크(더보기 등)가 존재하는 경우. -> endpoints에 각 리스트 URL 담기.
    - **Priority 2 (FOUND_SINGLE)**: '{region_name} 정책'을 한눈에 볼 수 있는 통합 검색/목록 페이지.
    JSON 응답 형식:
    {{
        "action": "FOUND_SINGLE" | "FOUND_MULTI" | "NAVIGATE" | "FAIL",
        "target_url": "이동할 URL (SINGLE/NAVIGATE용)",
        "endpoints": ["URL1", "URL2"] (MULTI일 때만),
        "reason": "판단 근거"
    }}
    """
    
    try:
        # 가성비 모델 사용 (Flash Lite)
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite', 
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"   ❌ 제미나이 에러: {e}")
        return {"action": "FAIL", "reason": "API Error"}

async def navigate_single_candidate(region_name, browser, start_url):
    page = await browser.new_page()
    current_url = start_url
    
    try:
        for depth in range(MAX_DEPTH + 1):
            print(f"   탐색 중... [Depth {depth}] {current_url}")

            if "error" in current_url.lower() or "404" in current_url:
                print(f"   ⚠️ 에러 페이지 감지: {current_url}")
                break

            try:
                await page.goto(current_url, wait_until="load", timeout=30000)
                await asyncio.sleep(2)
                current_url = page.url 
            except Exception:
                print(f"   ⚠️ 접속 실패: {current_url}")
                break

            page_text = await get_page_summary(page)
            links = await extract_links_from_page(page, current_url)
            decision = await ask_gemini_for_navigation(region_name, current_url, links, page_text, depth)
            
            action = decision.get("action")
            target = decision.get("target_url")
            endpoints = decision.get("endpoints", [])
            reason = decision.get("reason")
            
            print(f"   🤖 제미나이: {action} -> {reason}")
            
            async def move_to_target(url_to_go):
                if not url_to_go or "http" not in url_to_go: return False
                try:
                    if "#none" in url_to_go or "javascript" in url_to_go:
                        target_link = next((l for l in links if l['url'] == url_to_go), None)
                        if target_link:
                            await page.get_by_role("link", name=target_link['text']).first.click()
                            return True
                    else:
                        await page.goto(url_to_go, wait_until="load", timeout=30000)
                        return True
                except:
                    return False
                return False

            if action == "FOUND_SINGLE":
                final_url = urljoin(page.url, target) if target and "#none" not in target else current_url
                print(f"   🎉 단일 게시판 발견: {final_url}")
                return {"type": "single", "url": final_url, "reason": reason}

            elif action == "FOUND_MULTI":
                # [수정] 멀티 엔드포인트도 모두 절대 경로로 변환하여 저장
                abs_endpoints = [urljoin(page.url, ep) for ep in endpoints]
                print(f"   🎉 멀티 카테고리 발견 ({len(abs_endpoints)}개)")
                return {"type": "multi", "endpoints": abs_endpoints, "reason": reason}
            
            elif action == "NAVIGATE":
                if not target:
                    # 강제 키워드 탐색
                    keywords = ["더보기", "전체보기", "목록", "검색", "정책", region_name]
                    for kw in keywords:
                        fallback = next((l['url'] for l in links if kw in l['text']), None)
                        if fallback:
                            target = fallback; break
                
                if target:
                    success = await move_to_target(target)
                    if success:
                        await asyncio.sleep(2)
                        current_url = page.url
                        continue
                break
            else:
                break
                
    except Exception as e:
        print(f"   ❌ 런타임 오류: {e}")
    finally:
        await page.close()
    
    return None
async def select_the_best_one(region_name, findings):
    """
    여러 후보 중 '지역 특화' 및 '정보량' 기준으로 우승자를 뽑습니다.
    """
    prompt = f"""
    [{region_name}]의 청년 정책을 수집하기에 가장 완벽한 곳을 골라줘.

    [후보 목록]: {json.dumps(findings, ensure_ascii=False, indent=2)}

    [🏆 우승 기준]:
    1. **지역 브랜딩**: 설명에 "{region_name}"이 명확히 포함된 곳이 중앙정부 링크보다 우위.
    2. **정보의 양**: 단순 공지사항보다는 '멀티 카테고리'나 '통합 검색' 페이지가 우위.
    3. **정확성**: '청소년' 관련 사이트는 탈락.

    JSON 응답: {{ "best_index": 0 또는 1 }}
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        idx = json.loads(response.text)['best_index']
        return findings[idx]
    except:
        # 에러 시 멀티가 있으면 멀티 우선, 아니면 첫 번째
        for f in findings:
            if f['type'] == 'multi': return f
        return findings[0] if findings else None

async def process_region(region_data):
    name = region_data['nm']
    candidate_urls = region_data.get('urls', [])
    
    print(f"\n🚀 [{name}] 정밀 탐색 시작")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        findings = [] 
        for url in candidate_urls:
            print(f" 👉 진입: {url}")
            result = await navigate_single_candidate(name, browser, url)
            if result:
                findings.append(result)
        
        await browser.close()
        
        if not findings:
            return {"nm": name, "board_data": None, "status": "failed"}
        
        # 토너먼트 진행
        if len(findings) > 1:
            winner = await select_the_best_one(name, findings)
        else:
            winner = findings[0]
            
        print(f" 🏆 최종 우승: {winner}")
        
        if winner['type'] == 'multi':
            return {"nm": name, "type": "multi", "endpoints": winner['endpoints'], "status": "success"}
        else:
            return {"nm": name, "type": "single", "board_url": winner['url'], "status": "success"}

async def main():
    if not os.path.exists(INPUT_FILE):
        print("❌ discovered_urls.json 파일이 없습니다.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        targets = json.load(f)

    print(f"🔥 총 {len(targets)}개 지역에 대한 '지능형 탐색'을 시작합니다.")
    print("---------------------------------------------------")

    results = []
    
    for i, target in enumerate(targets):
        region_name = target['nm']
        print(f"\n[{i+1}/{len(targets)}] 🚩 {region_name} 처리 중")
        
        try:
            result = await process_region(target)
            results.append(result)
            
            # 중간 저장 (Incremental Save)
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"   💾 {region_name} 저장 완료")

        except Exception as e:
            print(f"   💥 {region_name} 처리 중 치명적 오류: {e}")
        
        # 서버 부하 방지 휴식
        print("   ☕ 잠시 숨 고르기 (3초)...")
        await asyncio.sleep(3)

    print("\n" + "="*50)
    print(f"✨ 모든 사냥 종료! {OUTPUT_FILE} 확인 바람.")
    print(f"✨ 총 발견된 지역: {len(results)}")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(main())