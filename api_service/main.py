from fastapi import FastAPI
import os

# .env 파일에서 PROJECT_NAME이라는 이름을 찾아봅니다.
app = FastAPI(title=os.getenv("PROJECT_NAME", "Youth Asset Manager"))

# 주소의 맨 끝(/)으로 GET(조회) 요청을 보내면, 바로 아래 있는 함수를 실행
@app.get("/")
def read_root():
    # 파이썬의 딕셔너리를 반환하면, FastAPI가 알아서 예쁜 JSON 형식으로 변환해 브라우저에 쏴줌
    return {
        "message": "Hello World! Youth Asset Manager API is running.",
        "version": os.getenv("API_V1_STR", "/api/v1")
    }

# 나중에 서버가 너무 힘들어하거나 죽었을 때, 관리 시스템이 이 주소로 툭툭 말을 검.
# {"status": "healthy"}라고 대답이 오면 "아, 아직 살아있구나!" 하고 안심하는 용도.
@app.get("/health")
def health_check():
    return {"status": "healthy"}