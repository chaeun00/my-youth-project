import json
import os
import time
import random
import requests
import boto3  # AWS Lambda 호출용
from datetime import datetime  # 시간 출력을 위해 추가
from concurrent.futures import ThreadPoolExecutor, as_completed # 병렬 처리를 위한 핵심

# AWS 및 경로 설정
lambda_client = boto3.client('lambda', region_name=os.getenv('AWS_DEFAULT_REGION', 'ap-northeast-2'))
LAMBDA_FUNCTION_NAME = "GoogleSearch_clone"
BASE_DIR = "data_engineering/scraper"
SEED_FILE = os.path.join(BASE_DIR, "seed_targets.json")
MAPPED_URL_FILE = os.path.join(BASE_DIR, "discovered_urls.json")
YOUTH_KEYWORDS = "청년포털"

def save_to_json(data):
    """데이터를 JSON 파일로 안전하게 저장하는 공통 함수"""
    with open(MAPPED_URL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def call_lambda_clone(query):
    """람다 분신에게 검색 명령을 내립니다."""
    try:
        response = lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType='RequestResponse',
            Payload=json.dumps({"query": query})
        )
        
        # [해결] 딱 한 번만 읽어서 변수에 저장합니다.
        raw_payload = response['Payload'].read().decode('utf-8')
        
        # 이제 변수를 사용하므로 몇 번을 써도 안전합니다.
        if not raw_payload:
            print(f"   ⚠️ 응답이 비어있습니다 (Timeout 의심): {query}", flush=True)
            return None

        result = json.loads(raw_payload)
        
        # 람다 내부에서 에러가 발생했을 경우
        if result.get('errorMessage'):
            print(f"   ❌ 람다 내부 로직 에러: {result['errorMessage']}", flush=True)
            return None

        # 정상적인 statusCode 200 확인
        if result.get('statusCode') == 200:
            body = result.get('body', {})
            # 람다 설정에 따라 body가 문자열일 수도 있으니 안전하게 처리
            if isinstance(body, str):
                body = json.loads(body)
            
            urls = body.get('urls', [])
            return urls[0] if urls else None
            
        else:
            print(f"   ⚠️ 람다 응답 실패 ({result.get('statusCode')}): {raw_payload}", flush=True)
            return None

    except Exception as e:
        print(f"   ❌ 통신/파싱 에러 ({query}): {str(e)}", flush=True)
        return None

def validate_and_follow_redirect(url):
    """
    발견된 URL이 리다이렉트되는지 확인하고 최종 도착지 주소를 반환합니다.
    """
    try:
        # HEAD 요청으로 리다이렉트 경로만 빠르게 추적 (body는 안 읽음)
        response = requests.head(url, allow_redirects=True, timeout=5)
        final_url = response.url
        
        if url.strip('/') != final_url.strip('/'):
            print(f"  🔄 자가 치유 작동: {url} -> {final_url}")
            return final_url
        return url
    except Exception as e:
        # 접속 실패 시 기존 URL 유지 혹은 None 반환
        return url

def process_single_target(target):
    """기관 1개에 대해 청년 포털 1개의 쿼리만 정밀 타격합니다."""
    name = target['nm']
    is_hub = target.get('type') == 'hub'
    
    # 허브(정부24/고용24)는 이름 그대로, 지자체는 '청년포털' 키워드 추가
    query = f"{name} { YOUTH_KEYWORDS if not is_hub else ''}".strip()
    
    found_url = call_lambda_clone(query)
    
    if found_url:
        # 허브는 물론, 지자체 포털도 주소 이전을 대비해 리다이렉트 확인
        # allow_redirects=True를 통해 최종 목적지를 확보합니다.
        found_url = validate_and_follow_redirect(found_url)
            
    return {"nm": name, "url": found_url}

def discover_urls():
    # 1. Seed 타겟 로드
    if not os.path.exists(SEED_FILE):
        print(f"❌ {SEED_FILE} 파일이 없습니다. 먼저 seed_generator를 실행하세요.")
        return

    with open(SEED_FILE, "r", encoding="utf-8") as f:
        seed_targets = json.load(f)

    # 2. 결과 저장용 바구니 (매번 새로 시작하여 덮어쓰기)
    discovered_data = []

    # 매번 seed_targets 전체를 대상으로 실행합니다.
    targets_to_process = seed_targets
    print(f"🚀 총 {len(targets_to_process)}개 기관에 대해 실시간 주소 검증 및 사냥을 시작합니다.")
    print(f"🚀 사냥 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # --- 병렬 처리 핵심 설정 ---
    MAX_WORKERS = 20  # 동시에 돌릴 분신(람다)의 수.
    SAVE_INTERVAL = 10  # 19개뿐이므로 저장 주기를 10개로 조정
    success_count = 0   # 이번 실행에서 성공한 개수 카운트
    
    print(f"🔥 분신 {MAX_WORKERS}명을 투입하여 {len(targets_to_process)}개 기관을 사냥합니다.")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_target = {executor.submit(process_single_target, t): t for t in targets_to_process}

        for future in as_completed(future_to_target):
            target_info = future_to_target[future]
            try:
                result = future.result()
                discovered_data.append(result)
                success_count += 1
                
                print(f"✅ [{result['nm']}] 완료 ({success_count}/{len(targets_to_process)})")
                
                # 중간 저장 (혹시 모를 오류 대비)
                if success_count % SAVE_INTERVAL == 0:
                    save_to_json(discovered_data)
                    
            except Exception as exc:
                print(f"❌ {target_info['nm']} 처리 중 오류: {exc}")

    # --- 최종 저장 (기존 파일을 최신 정보로 완전히 덮어씀) ---
    save_to_json(discovered_data)
    print(f"\n✨ 사냥 종료! 총 {len(discovered_data)}개 기관의 최신 주소를 확보했습니다.")
    print(f"✨ 사냥 종료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
if __name__ == "__main__":
    discover_urls()