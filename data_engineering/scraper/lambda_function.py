import json
import requests
import os

SERPER_API_KEY = os.getenv('SERPER_API_KEY')

def lambda_handler(event, context):
    query = event.get('query', '')
    if not query:
        return {'statusCode': 400, 'body': {'message': '명령이 없습니다.'}}

    url = "https://google.serper.dev/search"
    expanded_query = f"{query} (site:go.kr OR site:or.kr OR site:kr)"
    payload = json.dumps({
        "q": expanded_query,
        "gl": "kr",
        "hl": "ko"
    })
    headers = {
        'X-API-KEY': SERPER_API_KEY,
        'Content-Type': 'application/json'
    }

    try:
        response = requests.post(url, headers=headers, data=payload, timeout=10)
        data = response.json()
        
        discovered_urls = []
        if "organic" in data and len(data["organic"]) > 0:
            # 검색 결과 중 신뢰할 수 있는 도메인 패턴을 가진 링크만 추출
            trusted_patterns = [".go.kr", ".or.kr", ".kr"]
            
            for item in data["organic"]:
                link = item["link"]
                # 링크에 우리가 원하는 도메인이 포함되어 있는지 확인
                if any(pattern in link for pattern in trusted_patterns):
                    discovered_urls.append(link)
                    if len(discovered_urls) >= 1: break # 1개만 찾으면 즉시 종료

        return {
            'statusCode': 200,
            'body': {
                'query': query,
                'urls': discovered_urls
            }
        }
    except Exception as e:
        return {'statusCode': 500, 'body': {'message': str(e)}}