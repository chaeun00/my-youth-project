import sys
import os
import requests
import xml.etree.ElementTree as ET
import json
import time
from dotenv import load_dotenv
from urllib.parse import unquote # 키 값을 안전하게 처리하기 위해 필요합니다.
from constants import METRO_LIST

load_dotenv()  # .env 파일에 숨겨둔 API 키를 가져옵니다.

# 경로 및 URL 설정
BASE_DIR = "data_engineering/scraper"
CHECKPOINT_FILE = os.path.join(BASE_DIR, "head_orgs_checkpoint.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "head_orgs_final.json")
API_URL = "https://apis.data.go.kr/1741000/StanOrgCd2/getStanOrgCdList2"

# --- [공통 에러 처리 함수] ---
def check_http_error(response):
    """HTTP 연결 상태를 확인합니다."""
    if response is None or response.status_code != 200:
        status = response.status_code if response else "Unknown"
        print(f"🔥 [CRITICAL] 서버 접속 불가 (HTTP {status})")
        return False
    return True

def handle_api_error(code, msg):
    """에러 코드의 성격에 따라 프로그램의 운명을 결정합니다."""
    print(f"🚨 API 메시지 [{code}]: {msg}")
    
    # 전처리: "INFO-0" 또는 "ERROR-337"에서 숫자 부분만 추출
    try:
        # '-'로 나누고 마지막 요소를 가져온 뒤 숫자로 변환
        code_int = int(str(code).split('-')[-1])
    except (ValueError, IndexError):
        print(f"   -> ❓ 분석할 수 없는 코드 형식입니다: {code}")
        return -1 # 분석 불가 시 안전하게 -1 반환

    if code_int in [290, 300]:
        print("   -> 🔑 인증키(API KEY) 권한 에러. .env를 확인하세요.")
        sys.exit(1)
    elif code_int in [310, 333, 336]:
        print(f"   -> 🐛 파라미터 혹은 요청 건수 에러. 코드를 수정해야 합니다.")
        sys.exit(1)
    elif code_int in [500, 600, 601]:
        print("   -> ⏳ 서버/DB 장애: 재시도가 필요합니다.")
    elif code == 337:
        print("   -> 🛑 일일 트래픽 초과. 수집을 안전하게 중단합니다.")
    else:
        print("   -> ❓ 정의되지 않은 에러가 발생했습니다.")
    
    return code_int

def check_app_error(root, current_count=0):
    """API 응답 본문(Header)의 결과 코드를 확인합니다. (0: 정상, 그 외: 비정상)"""
    head = root.find(".//head")
    if head is None:
        # 1. <row> 태그가 하나라도 있는지 확인
        if root.find(".//row") is not None:
            return 0 # head는 없는데 데이터는 있다면 일단 진행 (드문 경우)
            
        # 2. 데이터도 없고 head도 없는데, 이미 데이터가 충분히(예: 13만개) 쌓였다면?
        if current_count >= 130000:
            print("🏁 [종료 근거] 데이터 수집 목표치 도달 및 빈 응답 확인.")
            return 200 # 정상 종료로 간주
            
        # 3. 데이터도 없는데 쌓인 것도 없다면? 이건 에러입니다.
        print("🚨 [에러] 유효한 데이터를 찾을 수 없고 수집된 데이터도 부족합니다.")
        return -1
    
    result_node = head.find("RESULT")
    if result_node is None:
        print("❌ [ERROR] <head> 내부에 <RESULT> 태그가 존재하지 않습니다.")
        return -1

    result_code = result_node.findtext("resultCode")
    result_msg = result_node.findtext("resultMsg")
        
    # 정상 코드는 "0" 혹은 "00" 입니다.
    if result_code not in ["INFO-0"]:
        return handle_api_error(result_code, result_msg)
    
    return 0

# --- [주요 로직 함수] ---

def get_typemid_by_code(service_key, org_cd):
    """특정 기관 코드를 조회하여 해당 기관의 분류명(typemid_nm)을 반환합니다."""
    params = {
        "ServiceKey": unquote(service_key),
        "pageNo": 1, "numOfRows": 1, "type": "xml", 
        "org_cd": org_cd
    }
    try:
        response = requests.get(API_URL, params=params, timeout=10)
        if not check_http_error(response): return None
        
        root = ET.fromstring(response.content)
        if check_app_error(root) != 0: return None
        
        row = root.find(".//row")
        return row.findtext("typemid_nm") if row is not None else None
    except Exception as e:
        print(f"❌ [{org_cd}] 조회 중 오류: {e}")
        return None

def validate_api_status(service_key):
    """중앙행정기관과 광역자치단체의 분류명을 동적으로 확보합니다."""
    print("🧪 API 동적 분류명 확보 시작...")
    
    # 1. 중앙행정기관 기준: 행정안전부 (1741000)
    central_mid = get_typemid_by_code(service_key, "1741000")
    
    if central_mid:
        print(f"✅ 카나리아 테스트 통과")
        print(f"   - 중앙 분류명: {central_mid}")
        return True, central_mid
    
    return False, None


def fetch_head_gov_data(service_key):
    # .env에서 키를 가져온 뒤, 혹시 모르니 unquote로 디코딩 상태를 보장합니다.
    
    checkpoint = load_checkpoint()
    head_orgs = checkpoint["data"]  # 수집한 모든 기관 데이터(<row>)를 담을 바구니(리스트)입니다.
    page_no = checkpoint["last_page"] + 1
    num_of_rows = 1000 # 성능을 위해 1000으로 상향 제안하셨던 값 반영
    total_raw_count = (page_no - 1) * num_of_rows   # 지금까지 처리한 전체 기관 수입니다.
    service_key_decoded = unquote(service_key)
    
    while True:      # 모든 데이터를 다 가져올 때까지 무한 반복합니다.
        # URL 뒤에 쿼리 스트링을 직접 붙여서 보냅니다.
        # 이렇게 해야 API 게이트웨이가 인증키를 정확히 인식합니다.
        query_params = (
            f"ServiceKey={service_key_decoded}"
            f"&type=xml"
            f"&pageNo={page_no}"
            f"&numOfRows={num_of_rows}"
            f"&stop_selt=0" # 사용: 0, 폐지: 1
        )
        full_url = f"{API_URL}?{query_params}"
        
        # --- [차단 방지 1] 재시도 로직 (Retry Logic) ---
        success = False
        for retry in range(1, 6):
            try:
                print(f"📡 {page_no}페이지 호출 중... (시도 {retry}/5)")
                response = requests.get(full_url, timeout=30)
                response.raise_for_status()

                if not check_http_error(response):
                    break
                
                root = ET.fromstring(response.content)

                # 2. API 앱 레벨 에러 체크 (트래픽 초과 등 확인)
                status = check_app_error(root, len(head_orgs))
                if status == 0:
                    # [정상] 데이터 파싱 진행
                    success = True
                    break
                elif status == 200:
                    # [핵심] 데이터가 끝났음을 감지하면 즉시 루프 종료
                    print("🏁 [INFO] 더 이상 가져올 데이터가 없습니다. 수집을 종료합니다.")
                    return head_orgs
                elif status == 337:
                    # 트래픽 초과는 리트라이 해도 소용없으니 바로 저장 후 종료
                    return head_orgs 
                elif status in [500, 600, 601]:
                    # 서버가 아프다고 하면 에러를 발생시켜 아래 except 문으로 보내 재시도
                    raise Exception(f"API Server Error ({status})")

            except Exception as e:
                wait_time = retry * 30 
                print(f"⚠️ 에러 발생 ({e}). {wait_time}초 후 재시도합니다.")
                time.sleep(wait_time)
        
        if not success:
            print(f"❌ {page_no}페이지 최종 실패. 중단합니다.")
            break
            
        total_count = int(root.find('.//totalCount').text)
        rows = root.findall('.//row')
            
        if not rows: break

        for row in rows:
            total_raw_count += 1
            head_orgs.append({
            "nm": row.findtext('full_nm'),
            "mid": row.findtext('typemid_nm'),
            "rank": row.findtext('rank_no'),
            "org": row.findtext('org_cd'),
            "rep": row.findtext('rep_cd'),
            "high": row.findtext('high_cd')
        })
            
        save_checkpoint(head_orgs, page_no)
        print(f"✅ {page_no}페이지 완료 (핵심 기관 누적: {len(head_orgs)}개 / 전체 탐색: {total_raw_count})")

        if total_raw_count >= total_count: break
            
        page_no += 1
        time.sleep(1) # 서버가 숨 쉴 시간을 줍니다.
        
    return head_orgs

def extract_seed_targets(all_data, central_name):
    """
    13만 개 데이터 중 '중앙행정기관'이면서 '최상위 기관(high_cd=0000000)'인 
    진짜 부/처/청만 추출합니다.
    """
    central = [
        item for item in all_data 
        if item['mid'] == central_name and item['high'] == "0000000"
    ]
    
    return central

# --- [기타 헬퍼 함수] ---
def save_checkpoint(data, last_page):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_page": last_page, "data": data}, f, ensure_ascii=False, indent=2)

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"last_page": 0, "data": []}

if __name__ == "__main__":
    key = os.getenv("GOV_API_KEY")

    # 시작 전 딱 한 번 검사
    is_ok, target_mid_name = validate_api_status(key)

    if is_ok:
        all_results = fetch_head_gov_data(key)
        central_seeds = extract_seed_targets(all_results, target_mid_name)
        metro_seeds = [{"nm": name, "mid": "광역자치단체"} for name in METRO_LIST]
        seed_targets = central_seeds + metro_seeds
        
        # 4. 전체 데이터 저장 (백업용)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
            
        # 5. Seed 타겟만 따로 저장 (실제 검색용 핵심 파일)
        SEED_FILE = os.path.join(BASE_DIR, "seed_targets.json")
        with open(SEED_FILE, "w", encoding="utf-8") as f:
            json.dump(seed_targets, f, ensure_ascii=False, indent=2)
            
        print(f"\n🎉 수집 및 필터링 완료!")
        print(f"   - 전체 탐색 기관: {len(all_results)}개")
        print(f"   - 🎯 추출된 Seed 타겟: {len(seed_targets)}개")
        print(f"   - 핵심 리스트가 {SEED_FILE}에 저장되었습니다.")
    else:
        # 검증 실패 시 로그 출력
        print("⚠️ 검증 단계에서 실패하여 수집을 시작하지 못했습니다.")