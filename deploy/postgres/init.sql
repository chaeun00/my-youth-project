-- 1. 테이블 생성 (정형 + 비정형 하이브리드 구조)
CREATE TABLE IF NOT EXISTS scraped_data (
    id SERIAL PRIMARY KEY,
    source_url TEXT NOT NULL,          -- 데이터를 가져온 원본 주소
    title TEXT,                        -- 제목
    content TEXT,                      -- 주요 텍스트 내용
    
    -- JSONB: 사이트마다 다른 추가 필드를 유연하게 저장 (작성자, 태그, 조회수 등)
    raw_metadata JSONB,                
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 2. URL 검색 최적화를 위한 인덱스 (B-Tree)
CREATE INDEX IF NOT EXISTS idx_scraped_data_url ON scraped_data(source_url);

-- 3. JSONB 내부 데이터 검색 최적화를 위한 인덱스 (GIN)
-- 이 인덱스가 있으면 JSON 내부의 특정 키/값 검색이 비약적으로 빨라집니다.
CREATE INDEX IF NOT EXISTS idx_scraped_data_metadata ON scraped_data USING GIN (raw_metadata);