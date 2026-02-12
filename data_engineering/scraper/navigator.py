import json
import os
import asyncio
import re
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from google import genai

# === 1. 설정 영역 ===
BASE_DIR = "data_engineering/scraper"
INPUT_FILE = os.path.join(BASE_DIR, "discovered_urls.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "target_boards.json")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

client = genai.Client(api_key=GOOGLE_API_KEY)
MAX_DEPTH = 2

async def extract_links_from_page(page, base_url):
    """링크 추출 및 1차 키워드 필터링"""
    IGNORE_KEYWORDS = ["공지사항", "청소년", "아동", "로그인", "회원가입", "이용약관", "개인정보", "페이스북", "인스타그램", "오시는길"]
    TARGET_KEYWORDS = ["청년정책", "지원사업", "사업안내", "모집공고", "정책검색", "일자리", "주거", "금융", "복지", "참여"]
    
    links = await page.evaluate('''() => {
        return Array.from(document.querySelectorAll('a')).map(a => ({
            text: a.innerText.trim(),
            href: a.href
        }));
    }''')
    
    filtered_links = []
    seen_urls = set()

    for link in links:
        text = link['text'].replace('\n', ' ')
        href = link['href']
        
        if not text or not href or href.startswith('javascript') or href == base_url:
            continue
            
        full_url = urljoin(base_url, href)
        if full_url in seen_urls: continue
        
        if any(bad in text for bad in IGNORE_KEYWORDS):
            continue
            
        if any(good in text for good in TARGET_KEYWORDS) or "바로가기" in text or "홈페이지" in text:
            filtered_links.append({"text": text, "url": full_url})
            seen_urls.add(full_url)
            
    return filtered_links[:50] # 멀티 엔드포인트를 찾으려면 후보군을 좀 더 넉넉히 줍니다.

async def ask_gemini_for_navigation(current_url, links, depth):
    """
    멀티 엔드포인트(FOUND_MULTI) 감지 능력 추가
    상세페이지 함정 탈출 지시
    청소년 원천 차단
    """
    prompt = f"""
    너는 웹 구조를 꿰뚫어 보는 '수석 데이터 엔지니어'이자 '지능형 네비게이터'야.
    목표: [실제 청년 정책 리스트가 가장 풍부하게 담긴 페이지]를 찾는 것.

    [현재 위치]: {current_url}
    [링크 목록]: {json.dumps(links, ensure_ascii=False, indent=2)}

    [🚫 3대 금지령 - 위반 시 즉시 FAIL]:
    1. **연령대 오류**: '청소년', '중고생', '어린이' 관련 사이트는 즉시 FAIL. (세종 사례 방지)
    2. **알맹이 없음**: '추진전략', '법령', '조례', '기관소개' 등 정보성 페이지는 FAIL.
    3. **단일 공고**: 'XX사업 신청', 'XX지원금 안내' 등 특정 사업 하나만 설명하는 상세 페이지는 FOUND가 아님. 반드시 상위 목록으로 NAVIGATE해. (대전 사례 방지)

    [🚨 필수 준수 사항 - 껍데기(#none) 돌파 및 지역 우선]:
    - **하위 링크 추적**: 상위 카테고리(예: 청년정책검색) 주소가 `#none`이나 `javascript`여도 절대 포기하지 마. 그 아래에 유효한 URL을 가진 **'서울시 정책'**, **'대전 청년 사업'** 등의 하위 링크가 있다면 그것이 우리의 최종 타겟이야.
    - **지역 특화 우선순위**: 현재 우리가 사냥 중인 지역명이 포함된 정책 링크(예: 서울시 정책)를 중앙정부나 타 지역 정책보다 **압도적으로 우선시**해서 선택해.
    - **공지사항 금지**: 제목이 '공지사항'인 곳은 지양하고, 반드시 정책 전용 목록이나 검색 페이지를 찾아.
    
    [🎯 성공 케이스 우선순위 (매우 중요)]:
    - **Priority 1 (FOUND_SINGLE)**: '서울시 정책', '대전청년사업' 등 지역명이 붙은 **통합 검색/목록** 페이지. (최고의 노다지!)
    - **Priority 2 (FOUND_SINGLE)**: '청년정책검색', '전체보기', '모든정책' 등 한 페이지에서 필터링하여 모든 정책을 볼 수 있는 곳. (우리가 가장 선호하는 형태!)
    - **Priority 3 (FOUND_MULTI)**: [일자리/주거/교육] 등 생애주기별/분야별로 메뉴가 나뉘어 있는 포털 메인. 
       * 이때는 각 카테고리 메뉴들의 URL을 'endpoints' 리스트에 담아줘. (대구 사례 해결)
    - **Priority 4 (FOUND_SINGLE)**: 통합 검색은 없지만, '지원사업안내'처럼 정책만 모아둔 단일 게시판.

    JSON 응답 형식:
    {{
        "action": "FOUND_SINGLE" | "FOUND_MULTI" | "NAVIGATE" | "FAIL",
        "target_url": "FOUND_SINGLE 또는 NAVIGATE일 때 이동할 URL",
        "endpoints": ["MULTI일 때만 채우는 리스트"],
        "reason": "구조적 분석 근거 (예: 통합 검색 페이지가 존재하므로 멀티 카테고리보다 우선 선택함)"
    }}
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash', # 가성비 모델 사용 시 'gemini-2.5-flash-lite'로 변경 가능
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"   ❌ 제미나이 분석 에러: {e}")
        return {"action": "FAIL"}

async def navigate_single_candidate(browser, start_url):
    page = await browser.new_page()
    current_url = start_url
    
    try:
        for depth in range(MAX_DEPTH + 1):
            print(f"   탐색 중... [Depth {depth}] {current_url}")
            try:
                # [대구] 리다이렉트 대기 및 URL 갱신
                await page.goto(current_url, wait_until="load", timeout=100000)
                await asyncio.sleep(2)
                current_url = page.url 
            except Exception:
                print(f"   ⚠️ 접속 실패: {current_url}")
                break

            links = await extract_links_from_page(page, current_url)
            decision = await ask_gemini_for_navigation(current_url, links, depth)
            
            action = decision.get("action")
            target = decision.get("target_url")
            endpoints = decision.get("endpoints", [])
            reason = decision.get("reason")
            
            print(f"   🤖 제미나이: {action} -> {reason}")
            
            if action == "FOUND_SINGLE":
                final_url = target if target else current_url
                print(f"   🎉 단일 게시판 발견: {final_url}")
                return {"type": "single", "url": final_url, "reason": reason}

            elif action == "FOUND_MULTI":
                # [대구 해결] 여러 카테고리 URL을 한 번에 반환
                print(f"   🎉 멀티 카테고리 발견 ({len(endpoints)}개): {endpoints}")
                return {"type": "multi", "endpoints": endpoints, "reason": reason}
            
            elif action == "NAVIGATE":
                if target and target != current_url:
                    current_url = target
                    continue
                else:
                    break
            else:
                break
                
    except Exception as e:
        print(f"   ❌ 오류: {e}")
    finally:
        await page.close()
    
    return None

async def select_the_best_one(findings):
    prompt = f"""
    두 후보 중 진짜배기를 골라줘.
    
    [후보 1]: {json.dumps(findings[0], ensure_ascii=False)}
    [후보 2]: {json.dumps(findings[1], ensure_ascii=False)}
    
    **URL보다 사이트 내용이 우선임. 사이트 내용에 금지 키워드가 있으면 탈락**

    [우승 기준]:
    - 청년 복지 사이트와 일반 복지 사이트가 충돌할 경우 청년 복지가 무조건 우승.
    - '멀티 카테고리(multi)'가 [일자리/주거/교육] 등 생애주기별로 잘 갖춰져 있다면 우승 유력.
    - '단일 게시판(single)'이라도 공식력이 높으면 우승 가능.
    
    [탈락 기준]:
    - 제목 혹은 카테고리가 정책이 아닌, '청소년', '이용기관', '센터', '공지사항' 관련에 치중된 경우

    JSON 응답: {{ "best_index": 0 또는 1 }}
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        idx = json.loads(response.text)['best_index']
        return findings[idx]
    except:
        # 에러 나면 그냥 첫 번째 것 선택 (또는 multi가 있으면 multi 우선)
        for f in findings:
            if f['type'] == 'multi': return f
        return findings[0]

async def process_region(region_data):
    name = region_data['nm']
    candidate_urls = region_data.get('urls', [])
    
    print(f"\n🚀 [{name}] 정밀 탐색 시작")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        findings = [] 
        for url in candidate_urls:
            print(f" 👉 후보지 진입: {url}")
            result = await navigate_single_candidate(browser, url)
            if result:
                findings.append(result)
        
        await browser.close()
        
        if not findings:
            return {"nm": name, "board_data": None, "status": "failed"}
        
        # 우승자 결정
        if len(findings) > 1:
            winner = await select_the_best_one(findings)
        else:
            winner = findings[0]
            
        print(f" 🏆 최종 우승: {winner}")
        
        # 최종 결과 저장 구조 정리
        if winner['type'] == 'multi':
            return {"nm": name, "type": "multi", "endpoints": winner['endpoints'], "status": "success"}
        else:
            return {"nm": name, "type": "single", "board_url": winner['url'], "status": "success"}

async def main():
    if not os.path.exists(INPUT_FILE):
        print("❌ discovered_urls.json 파일을 먼저 생성해주세요.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        targets = json.load(f)

    print(f"🔥 총 {len(targets)}개 지역에 대한 '지능형 탐색'을 시작합니다.")
    print("---------------------------------------------------")

    results = []
    
    # [변경] 테스트 필터(test_targets) 제거! -> targets 전체 순회
    for i, target in enumerate(targets):
        region_name = target['nm']
        print(f"\n[{i+1}/{len(targets)}] 🚩 {region_name} 진입")
        
        try:
            # 지역 탐색 실행
            result = await process_region(target)
            results.append(result)
            
            # [핵심] 한 지역이 끝날 때마다 파일에 '중간 저장'을 합니다.
            # 혹시라도 중간에 멈추더라도 데이터는 남습니다.
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"   💾 {region_name} 탐색 결과 저장 완료")

        except Exception as e:
            print(f"   💥 {region_name} 처리 중 치명적 오류: {e}")
            # 에러가 나도 다음 지역으로 넘어갑니다.
        
        # 구글 API 및 서버 부하를 줄이기 위한 매너 휴식 (3초)
        print("   ☕ 잠시 숨 고르기 (3초)...")
        await asyncio.sleep(3)

    print("\n" + "="*50)
    print(f"✨ 모든 사냥 종료! 최종 결과가 {OUTPUT_FILE}에 저장되었습니다.")
    print(f"✨ 총 발견된 지역 수: {len(results)}")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(main())