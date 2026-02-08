import json
import os
import time
import random
import boto3  # AWS Lambda 호출용
from concurrent.futures import ThreadPoolExecutor, as_completed # 병렬 처리를 위한 핵심
from constants import YOUTH_KEYWORDS, WELFARE_KEYWORDS, FINANCE_KEYWORDS

# AWS 및 경로 설정
lambda_client = boto3.client('lambda', region_name=os.getenv('AWS_DEFAULT_REGION', 'ap-northeast-2'))
LAMBDA_FUNCTION_NAME = "GoogleSearch_clone"
BASE_DIR = "data_engineering/scraper"
SEED_FILE = os.path.join(BASE_DIR, "seed_targets.json")
MAPPED_URL_FILE = os.path.join(BASE_DIR, "discovered_urls.json")

def save_to_json(data):
    """데이터를 JSON 파일로 안전하게 저장하는 공통 함수"""
    with open(MAPPED_URL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def call_lambda_clone(query):
    """람다 분신에게 검색 명령을 내립니다."""
    response = lambda_client.invoke(
         FunctionName=LAMBDA_FUNCTION_NAME,
         InvocationType='RequestResponse',
        Payload=json.dumps({"query": query})
    )
    result = json.loads(response['Payload'].read().decode('utf-8'))
        
    raw_payload = response['Payload'].read().decode('utf-8')
    print(f"DEBUG: {raw_payload}", flush=True) 
    return json.loads(raw_payload)

def process_single_target(target):
    """기관 1개에 대해 2개의 쿼리를 처리하는 작업 단위"""
    name = target['nm']
    results = {"nm": name, "urls": {}}
    
    # 쿼리 정의
    queries = {
        "youth_portal": f"{name} {YOUTH_KEYWORDS[0]}",
        "welfare_finance": f"{name} {WELFARE_KEYWORDS[0]} {FINANCE_KEYWORDS[0]}"
    }

    for cat, query in queries.items():
        # 여기서 람다 호출
        found_url = call_lambda_clone(query)
        results["urls"][cat] = found_url
        # 람다 간 아주 짧은 간격 (구글 눈치 보기)
        time.sleep(random.uniform(1, 2))
        
    return results

def discover_urls():
    # 1. Seed 타겟 로드
    if not os.path.exists(SEED_FILE):
        print(f"❌ {SEED_FILE} 파일이 없습니다. 먼저 seed_generator를 실행하세요.")
        return

    with open(SEED_FILE, "r", encoding="utf-8") as f:
        seed_targets = json.load(f)

    # 2. 결과 저장용 바구니 (기존 결과가 있으면 로드)
    if os.path.exists(MAPPED_URL_FILE):
        with open(MAPPED_URL_FILE, "r", encoding="utf-8") as f:
            try:
                discovered_data = json.load(f)
            except json.JSONDecodeError:
                # 파일은 있는데 내용이 비어있으면 빈 리스트로 시작함
                discovered_data = []
    else:
        discovered_data = []
    # 이미 처리된 기관 제외 (중단 시 재시작 가능하도록)
    processed_names = {item['nm'] for item in discovered_data}
    remaining_targets = [t for t in seed_targets if t['nm'] not in processed_names]
    print(f"🚀 총 {len(remaining_targets)}개 남은 기관에 대해 사냥을 시작합니다.")

    # --- 병렬 처리 핵심 설정 ---
    MAX_WORKERS = 15  # 동시에 돌릴 분신(람다)의 수.
    SAVE_INTERVAL = 20  # 20개마다 저장
    success_count = 0   # 이번 실행에서 성공한 개수 카운트
    
    print(f"🔥 분신 {MAX_WORKERS}명을 동시에 투입하여 {len(remaining_targets)}개 기관을 사냥합니다.")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_target = {executor.submit(process_single_target, t): t for t in remaining_targets}

        for future in as_completed(future_to_target):
            target_info = future_to_target[future]
            try:
                result = future.result()
                discovered_data.append(result)
                success_count += 1
                
                print(f"✅ [{result['nm']}] 완료 ({success_count}/{len(remaining_targets)})")
                
                # --- [핵심] 20개마다 중간 저장 ---
                if success_count % SAVE_INTERVAL == 0:
                    save_to_json(discovered_data)
                    print(f"💾 중간 점검: {success_count}개 데이터를 파일에 기록했습니다.")
                    
            except Exception as exc:
                print(f"❌ {target_info['nm']} 처리 중 오류: {exc}")

    # --- [핵심] 모든 작업 종료 후 최종 저장 ---
    save_to_json(discovered_data)
    print(f"\n✨ 사냥 종료! 총 {len(discovered_data)}개 기관 확보 및 최종 저장 완료.")
    
if __name__ == "__main__":
    discover_urls()