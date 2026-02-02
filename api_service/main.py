import sys
import os
import uvicorn # 서버 실행을 위해 필요합니다

# 1. "지금 이 파일(main.py)이 있는 정확한 위치가 어디야?"
current_dir = os.path.dirname(os.path.abspath(__file__))
# 2. "한 층 위로 올라가면 프로젝트 전체를 다 볼 수 있는 루트(Root) 폴더네?"
root_dir = os.path.dirname(current_dir)
# 3. "이제 네 주소록(sys.path)에 이 루트 폴더를 추가해 줄게. 여기서 친구들을 찾아봐!"
sys.path.append(root_dir)

from fastapi import FastAPI
import json
from ml_engineering.inference.engine import PolicyInferenceEngine

app = FastAPI(title=os.getenv("PROJECT_NAME", "Youth Asset Manager"))

# 엔진 딱 한 번만 로드!
print("⏳ AI 모델을 불러오는 중입니다... (최초 1회 다운로드 포함)")
engine = PolicyInferenceEngine()
print("✅ 모델 준비 완료! 이제 요청을 받을 수 있습니다.")

@app.get("/test-policy")
def get_policy():
    # 1. 정제된 JSON 파일 읽기 (전처리 단계에서 생성된 데이터)
    with open("data/processed_gumi_policy.json", "r") as f:
        policy = json.load(f)
    
    # 2. AI 분석 수행: 제목과 혜택 내용을 합쳐서 엔진에게 카테고리 판단을 맡깁니다.
    # f-string을 사용하여 분석에 필요한 정보를 하나로 뭉쳐서 전달합니다.
    analysis = engine.predict_category(f"{policy['title']} {policy['eligibility']['benefit_summary']}")
    
    # 3. 최종 결과 응답: 원본 제목과 AI의 분석 데이터를 JSON 형태로 사용자에게 보여줍니다.
    return {"policy": policy['title'], "analysis": analysis}

# 서버 실행 스위치 (이게 있어야 python -m으로 실행했을 때 서버가 켜집니다)
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)