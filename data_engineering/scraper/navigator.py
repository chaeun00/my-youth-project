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
    # [세종 해결] 청소년, 아동 등은 아예 수집 단계에서 배제
    IGNORE_KEYWORDS = ["청소년", "아동", "초등", "중등", "고등", "로그인", "회원가입", "이용약관", "개인정보", "페이스북"]
    TARGET_KEYWORDS = ["공지", "사업", "소식", "정책", "지원", "프로그램", "신청", "목록", "일자리", "주거", "교육", "복지", "참여"]
    
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
    너는 웹 구조를 분석하는 '수석 데이터 엔지니어'야. 
    목표: [지역 청년 정책 목록]을 찾는 것.
    
    [현재 위치]: {current_url}
    [링크 목록]: {json.dumps(links, ensure_ascii=False, indent=2)}
    
    [⚠️ 엄격한 금지령]:
    1. '청소년', '중고생' 관련 링크는 보는 즉시 FAIL. (세종 주의)
    2. '청년정책 신청'과 같은 '신청'페이지나, 특정 사업명 하나만 있는 '상세 페이지'는 FOUND가 아님. 상위 목록으로 NAVIGATE해. (대전 주의)

    [⚠️ 필수 준수 사항]:
    - NAVIGATE를 선택했다면, 위 [링크 목록] 중 하나를 반드시 `target_url`에 복사해 넣어야 해.
    - '신청' 등의 페이지라면 상위 메뉴인 '청년정책'이나 'XX지역 청년사업' 링크를 찾아. 절대 빈카드로 두지 마!

    [🎯 판단 기준 - 두 가지 성공 케이스]:
    Case A. **FOUND_SINGLE**: '공지사항'이나 '사업안내'처럼 하나의 게시판에 모든 정책이 리스트로 있는 경우.
    Case B. **FOUND_MULTI** (대구 스타일): '일자리', '주거', '교육', '복지' 처럼 **카테고리별로 메뉴가 나뉘어 있는 포털 메인**인 경우.
       - 이때는 해당 카테고리 메뉴들의 URL을 'endpoints' 리스트에 담아줘.

    [🎯 판단 기준 - 실패 케이스]:
    Case C **FAIL**: 제목 혹은 카테고리가 정책이 아닌, '청소년', '이용기관', '센터', '일반 공지사항'에 치중된 경우

    JSON 응답:
    {{
        "action": "FOUND_SINGLE" | "FOUND_MULTI" | "NAVIGATE" | "FAIL",
        "target_url": "URL (SINGLE/NAVIGATE용)",
        "endpoints": ["URL1", "URL2", ...] (MULTI용, 위 링크 목록에서 발췌),
        "reason": "설명"
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
        print("❌ discovered_urls.json 없음")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        targets = json.load(f)

    test_targets = [t for t in targets if t['nm'] in ["대전광역시", "세종특별자치시", "대구광역시"]]
    
    results = []
    for target in test_targets:
        result = await process_region(target)
        results.append(result)
        await asyncio.sleep(2) # 예의상 휴식

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"\n✨ 완료! {OUTPUT_FILE} 확인 바람.")

if __name__ == "__main__":
    asyncio.run(main())