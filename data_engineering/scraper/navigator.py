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
    너는 [{region_name}]의 청년 정책 데이터를 수집하는 '수석 데이터 엔지니어'이자 '지능형 네비게이터'야.
    목표: **오직 [{region_name}] 지자체와 그 소속 자치구가 직접 시행하는 순수 정책 리스트** 이면서, **요약본이 아닌, 모든 정책이 나열되는 [진짜 목록 페이지]**를 찾는 것.

    [현재 상황]: {region_name} | {current_url} | Depth: {depth}/{MAX_DEPTH}
    [페이지 요약]: {page_text}
    [링크 후보]: {json.dumps(links, ensure_ascii=False, indent=2)}

    **경고**: URL에 'error', '404'가 포함되어 있다면 즉시 FAIL을 선언하고 탈출하라.
    *(주의 1: URL이 그럴듯해도 위 요약 내용에 '중앙정부', '전국공통', '청소년'이 주를 이룬다면 즉시 FAIL이다.)*
    *(주의 2: 단일 정책 페이지를 발견한 것 같다고 판단되더라도, 요약에 '더보기'나 '전체보기' 버튼이 있다면 현재 페이지는 '맛보기'일 뿐이다. 그 버튼의 주소가 `#none`이고 `onclick`이 있어도, 그걸 클릭해야 리스트가 나온다는 것을 인지하고 타겟으로 잡아라.)*

    [🚫 즉시 실패(FAIL) 기준 - 위반 시 미션 실패]:
    1. **중앙정부/타지역 배제**: '중앙정부', '정부24', '전국공통', '타 지자체' 정책은 쓰레기다. 절대 수집하지 마라.
    2. **연령대 오류**: 내용에 '청소년', '중고생', '아동', '노인'이 포함되면 즉시 FAIL.
    3. **행정/노이즈 제거**: 인사말, 조직도, 법령, 조례, 연구자료, 캘린더, 일정표 페이지는 무시하라.
    4. **가짜 리스트(BBS)**: 제목은 '공지사항'인데 내용이 '직원 채용', '입찰 공고', '단순 행사 안내'뿐이라면 정책 리스트가 아니다. 과감히 FAIL하라.
    5. **상세 페이지 함정**: 특정 사업 하나(예: XX지원금 신청)만 설명하는 상세 페이지는 목록이 아니다. 상위 목록으로 NAVIGATE하라.

    [🚨 기술적 돌파 지침]:
    1. **인트로(Intro) 돌파**: '정책정보 바로가기', '메인 입장', '홈페이지 바로가기' 버튼이 보이면 고민 없이 NAVIGATE하여 본진으로 진입하라.
    2. **#none 돌파**: '더보기' 링크의 href가 `#none`이어도 텍스트가 명확하면 그 버튼을 타겟으로 잡아라.
    3. **내용 기반 판단**: URL 키워드보다 [페이지 요약]에 담긴 **실제 텍스트 내용**을 보고 현재 지역의 정책이 맞는지 1순위로 판단하라.

    [🎯 성공(FOUND) 및 멀티 섹션 판정]:
    - **Priority 1 (FOUND_MULTI)**: 한 페이지에 '{region_name} 자체 정책'과 '자치구(구청) 정책' 섹션이 나뉘어 있고, 각 섹션의 건수(Count)가 표시된 '더보기 >' 링크가 따로 존재하는 경우. -> **중앙정부 링크는 빼고** 이 두 가지만 endpoints에 담아라.
    - **Priority 2 (FOUND_SINGLE)**: '{region_name} 정책'만 순수하게 모아둔 통합 검색/목록 페이지.

    JSON 응답 형식:
    {{
        "action": "FOUND_SINGLE" | "FOUND_MULTI" | "NAVIGATE" | "FAIL",
        "target_url": "이동할 URL (절대 경로)",
        "endpoints": ["오직 {region_name} 시청 정책 URL", "오직 {region_name} 산하 구청 정책 URL"],
        "reason": "중앙정부/타지역은 배제하고 {region_name} 순혈 정책 리스트만 선택한 근거"
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