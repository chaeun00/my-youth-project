import os
import requests
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from urllib.parse import unquote # 키 값을 안전하게 처리하기 위해 필요합니다.

load_dotenv()  # .env 파일에 숨겨둔 API 키를 가져옵니다.

def fetch_all_gov_data():
    # .env에서 키를 가져온 뒤, 혹시 모르니 unquote로 디코딩 상태를 보장합니다.
    raw_key = os.getenv("GOV_API_KEY")
    service_key = unquote(raw_key)
    url = "https://apis.data.go.kr/1741000/StanOrgCd2/getStanOrgCdList2" # 호출할 API 주소예요.
    
    all_rows = []    # 수집한 모든 기관 데이터(<row>)를 담을 바구니(리스트)입니다.
    page_no = 1      # 처음 시작할 페이지 번호입니다.
    num_of_rows = 500  # 한 번 요청할 때 가져올 데이터 개수예요.
    
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
        
        print(f"📡 {page_no}페이지 수집 중... (URL 직접 호출)")
        response = requests.get(full_url, timeout=30) # 타임아웃을 넉넉히 줍니다.
        
        if response.status_code != 200:
            print(f"❌ 서버 에러 발생 (Status: {response.status_code})")
            print(f"📄 에러 내용: {response.text}")
            break # 혹은 return []
            
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as e:
            print(f"❌ XML 파싱 실패: 데이터가 XML 형식이 아닙니다.")
            print(f"📄 받은 데이터 샘플: {response.text[:100]}")
            break

        # 2. 서버가 준 XML 텍스트를 파이썬이 이해할 수 있는 트리(Tree) 객체로 바꿉니다.
        root = ET.fromstring(response.content)
        
        # 3. [중요] 전체 결과 수(totalCount)를 찾습니다.
        # .find('.//totalCount')는 전체 XML에서 totalCount라는 이름의 태그를 딱 하나 찾는 기능입니다.
        total_count = int(root.find('.//totalCount').text)
        
        # 4. 현재 페이지에 들어있는 모든 기관 정보(<row>)를 리스트로 가져옵니다.
        # .findall('.//row')는 XML 안에 있는 모든 <row> 태그를 다 찾아서 '리스트'로 반환합니다.
        rows = root.findall('.//row')
        
        # 5. [if not rows] 만약 이번 페이지에서 찾은 기관(rows)이 하나도 없다면?
        if not rows:
            break  # 더 이상 가져올 데이터가 없다는 뜻이므로 반복문을 끝냅니다.
            
        # 6. 이번 페이지에서 찾은 rows들을 아까 만든 큰 바구니(all_rows)에 쏟아붓습니다.
        all_rows.extend(rows)
        
        # 7. 지금까지 모은 개수가 서버가 말한 전체 개수(totalCount)보다 같거나 많아졌나요?
        if len(all_rows) >= total_count:
            break  # 모든 데이터를 다 모았으므로 반복문을 끝냅니다.
            
        # 8. 아직 더 남아있다면, 다음 페이지 번호를 1 늘리고 다시 위로 올라가 반복합니다.
        page_no += 1
        
    return all_rows  # 최종적으로 전국 모든 행정기관 데이터가 담긴 바구니를 반환합니다.

if __name__ == "__main__":
    print("🚀 데이터 수집을 시작합니다. 잠시만 기다려 주세요...")
    try:
        results = fetch_all_gov_data()
        print(f"\n✅ 수집 완료!")
        print(f"📊 총 수집된 기관 수: {len(results)}개")
        
        # 첫 번째 데이터만 살짝 열어서 확인해보기
        if results:
            first_org_name = results[0].findtext('full_nm')
            print(f"🔍 첫 번째 수집 기관명: {first_org_name}")
            
    except Exception as e:
        print(f"❌ 에러 발생: {e}")