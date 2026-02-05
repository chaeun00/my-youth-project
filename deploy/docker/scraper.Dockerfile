# 가벼운 파이썬 이미지 사용
FROM python:3.10-slim

# 리눅스 전처리 도구 미리 설치 (나중을 위해!)
RUN apt-get update && apt-get install -y \
    curl \
    grep \
    sed \
    # PDF 변환 도구 (나중에 성능 비교할 때 사용)
    poppler-utils \ 
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 라이브러리 설치
COPY requirements/requirements-base.txt requirements/requirements-scraper.txt ./

RUN pip install --no-cache-dir -r requirements-scraper.txt

# 소스 복사
COPY . .

# 컨테이너가 바로 종료되지 않게 대기
CMD ["tail", "-f", "/dev/null"]