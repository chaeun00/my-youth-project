# 가벼운 파이썬 이미지 사용
FROM python:3.10-slim

# 리눅스 전처리 도구 및 Playwright에 필요한 시스템 의존성 설치
RUN apt-get update && apt-get install -y \
    curl \
    grep \
    sed \
    poppler-utils \ 
    # Playwright가 브라우저를 띄울 때 필요한 최소한의 라이브러리들
    libglib2.0-0 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 라이브러리 설정 파일 복사
COPY requirements/requirements-base.txt requirements/requirements-scraper.txt ./

# 1. Python 패키지 설치
RUN pip install --no-cache-dir -r requirements-scraper.txt

# 2. Playwright 전용 브라우저 및 의존성 설치 (핵심!)
# chromium만 설치해서 용량을 최적화합니다.
RUN playwright install chromium
RUN playwright install-deps chromium

# 소스 복사
COPY . .

# 컨테이너가 바로 종료되지 않게 대기
CMD ["tail", "-f", "/dev/null"]