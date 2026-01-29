import requests              # 웹사이트에 "데이터 좀 줘"라고 요청하는 도구입니다.
from bs4 import BeautifulSoup # 가져온 HTML 속에서 원하는 글자만 골라내는 '핀셋' 같은 도구입니다.
import os                    # 폴더 생성, 파일 저장 등 컴퓨터 시스템을 다루는 도구입니다.

def scrape_gumi_policy():
    # 1. 어디서 긁어올지 주소를 정합니다.
    url = "https://www.gumi.go.kr/reservation/www/anm/master/view.do?idx=3012&key=132"
    
    # 2. ⭐ headers: "저는 나쁜 로봇이 아닙니다"라고 말하는 신분증입니다. (아래 상세 설명)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        # 3. 실제로 웹사이트에 접속해서 데이터를 받아옵니다. 이때 신분증(headers)을 같이 보여줍니다.
        response = requests.get(url, headers=headers)
        
        # 4. 서버가 "404(없음)"나 "500(에러)"를 뱉으면 즉시 실행을 멈추고 에러 처리를 합니다.
        response.raise_for_status() 
        
        # 5. 사냥해온 날것의 HTML을 저장할 경로를 정합니다.
        raw_data_path = "data/raw_gumi_policy.html"
        
        # 6. 파일을 열어서(w: 쓰기 모드) 받아온 내용(response.text)을 저장합니다.
        with open(raw_data_path, "w", encoding="utf-8") as f:
            f.write(response.text)
            
        print(f"✅ 구미시 정책 데이터 사냥 성공! 저장 위치: {raw_data_path}")
        return True

    except Exception as e:
        # 에러가 나면 어떤 에러인지 출력합니다.
        print(f"❌ 데이터 사냥 실패: {e}")
        return False

# 7. ⭐ "여기서부터 시작해라!"라는 실행 시작점입니다. (아래 상세 설명)
if __name__ == "__main__":
    # 8. 'data'라는 이름의 폴더가 없으면 새로 만듭니다. (exist_ok=True는 이미 있으면 무시하라는 뜻)
    os.makedirs("data", exist_ok=True)
    
    # 9. 이제 위에서 만든 사냥 함수를 실행합니다.
    scrape_gumi_policy()