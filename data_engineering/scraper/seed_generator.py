import os
import requests
import xml.etree.ElementTree as ET
import json
import time
from dotenv import load_dotenv
from urllib.parse import unquote # 키 값을 안전하게 처리하기 위해 필요합니다.

load_dotenv()  # .env 파일에 숨겨둔 API 키를 가져옵니다.

# 저장 경로 설정
BASE_DIR = "data_engineering/scraper"
CHECKPOINT_FILE = os.path.join(BASE_DIR, "head_orgs_checkpoint.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "head_orgs_final.json")

def save_checkpoint(data, last_page):
    """중간 수집 결과를 저장합니다."""
    checkpoint = {"last_page": last_page, "data": data}
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)

def load_checkpoint():
    """이전에 저장된 기록이 있다면 불러옵니다."""
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"last_page": 0, "data": []}

def fetch_head_gov_data():
    # .env에서 키를 가져온 뒤, 혹시 모르니 unquote로 디코딩 상태를 보장합니다.
    raw_key = os.getenv("GOV_API_KEY")
    service_key = unquote(raw_key)
    url = "https://apis.data.go.kr/1741000/StanOrgCd2/getStanOrgCdList2" # 호출할 API 주소예요.
    
    # 중간 지점부터 시작
    checkpoint = load_checkpoint()
    head_orgs = checkpoint["data"]    # 수집한 모든 기관 데이터(<row>)를 담을 바구니(리스트)입니다.
    start_page = checkpoint["last_page"] + 1
    
    page_no = start_page    # 시작할 페이지 번호입니다.
    num_of_rows = 500       # 한 번 요청할 때 가져올 데이터 개수예요.
    total_raw_count = (page_no - 1) * num_of_rows  # 지금까지 처리한 전체 기관 수입니다.
    
    while True:      # 모든 데이터를 다 가져올 때까지 무한 반복합니다.
        # URL 뒤에 쿼리 스트링을 직접 붙여서 보냅니다.
        # 이렇게 해야 API 게이트웨이가 인증키를 정확히 인식합니다.
        query_params = (
            f"ServiceKey={service_key}"
            f"&type=xml"
            f"&pageNo={page_no}"
            f"&numOfRows={num_of_rows}"
            f"&stop_selt=0" # 사용: 0, 폐지: 1
        )
        full_url = f"{url}?{query_params}"
        
        # --- [차단 방지 1] 재시도 로직 (Retry Logic) ---
        retry_count = 0
        max_retries = 5
        response = None
        
        while retry_count < max_retries:
            try:
                print(f"📡 {page_no}페이지 호출 중... (시도 {retry_count + 1}/{max_retries})")
                response = requests.get(full_url, timeout=30)
                response.raise_for_status()
                break # 성공 시 루프 탈출
            except Exception as e:
                retry_count += 1
                wait_time = retry_count * 60 # 에러 시 1분, 2분... 점진적으로 대기 시간 증가
                print(f"⚠️ 에러 발생 ({e}). {wait_time}초 후 재시도합니다.")
                time.sleep(wait_time)
        
        if not response or response.status_code != 200:
            print("❌ 복구 불가능한 에러로 인해 수집을 중단합니다.")
            break
        
        # --- 데이터 처리 ---
        try:
            root = ET.fromstring(response.content)
            total_count = int(root.find('.//totalCount').text)
            rows = root.findall('.//row')
            
            if not rows: break

            for row in rows:
                total_raw_count += 1
                org_id = row.findtext('org_cd')
                rep_id = row.findtext('rep_cd')
                
                # [구조적 필터링] 자기 자신이 대표인 기관만 추출
                if org_id == rep_id:
                    head_orgs.append({
                        "org_cd": org_id,
                        "full_nm": row.findtext('full_nm'),
                        "rank": row.findtext('rank_no'),
                        "type": row.findtext('typemid_nm')
                    })
            
            # --- [중간 저장 2] 페이지 단위 체크포인트 ---
            save_checkpoint(head_orgs, page_no)
            print(f"✅ {page_no}페이지 완료 (핵심 기관 누적: {len(head_orgs)}개 / 전체 탐색: {total_raw_count})")

            if total_raw_count >= total_count: break
            
            # --- [차단 방지 3] Throttling ---
            page_no += 1
            time.sleep(1) # 서버가 숨 쉴 시간을 줍니다.

        except Exception as e:
            print(f"❌ 데이터 해석 중 에러: {e}")
            break
            
    return head_orgs

if __name__ == "__main__":
    results = fetch_head_gov_data()
    # 최종 결과 저장
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n🎉 수집 종료! 최종 {len(results)}개의 핵심 기관 리스트가 {OUTPUT_FILE}에 저장되었습니다.")