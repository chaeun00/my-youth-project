# 1. 기반 이미지 설정 (파이썬 3.9 슬림 버전)
FROM python:3.9-slim

# 2. 파이썬 관련 환경 변수 설정
# .pyc 파일을 생성하지 않도록 설정
ENV PYTHONDONTWRITEBYTECODE 1
# 로그가 실시간으로 출력되도록 설정
ENV PYTHONUNBUFFERED 1

# 3. 작업 디렉토리 설정 (컨테이너 내부의 폴더)
WORKDIR /app

# 4. 필수 리눅스 패키지 설치 (PostgreSQL 연결 및 빌드용)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 5. 의존성 파일 복사 및 설치
# (캐싱 효율을 위해 requirements.txt만 먼저 복사합니다)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 6. 전체 프로젝트 코드 복사
COPY . .

# 7. 서버 실행 (FastAPI 실행 명령)
# 8000번 포트로 열고, 코드가 수정되면 자동으로 재시작(--reload)
CMD ["uvicorn", "api_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]