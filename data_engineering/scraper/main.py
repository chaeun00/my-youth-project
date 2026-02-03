# 위에서 만든 DBHandler 클래스를 가져옵니다 (경로에 맞춰 import)
import os
from pathlib import Path
from dotenv import load_dotenv
from db_handler import DBHandler

env_path = Path(__file__).resolve().parent.parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# 환경 변수에서 값 가져오기
db_config = {
    "host": os.getenv("DB_HOST"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT")
}

def run_scraper():
    # 1. DB 비서 고용
    db = DBHandler(db_config)
    
    # 2. DB 연결 시작
    db.connect()

    try:
        # --- [가상 상황] 스크래퍼가 일을 해서 데이터를 가져왔다고 가정 ---
        # 실제로는 BeautifulSoup 등으로 긁어온 데이터가 이 자리에 들어감
        items_to_save = [
            {
                "title": "2026년 AI 트렌드",
                "content": "PostgreSQL과 파이썬의 조합은 여전히 강력합니다.",
                "url": "https://tech-blog.com/1",
                "extra": {"author": "Gemini", "category": "Tech"}
            }
        ]
        # ---------------------------------------------------------

        # 3. 가져온 데이터를 하나씩 DB에 쏘기
        for item in items_to_save:
            db.insert_scraped_data(
                title=item['title'],
                content=item['content'],
                source_url=item['url'],
                metadata=item['extra'] # 딕셔너리 통째로 JSONB 컬럼에 쏙!
            )

    finally:
        # 4. 일이 다 끝나면(성공하든 실패하든) 반드시 DB 연결을 닫아줌
        db.close()

if __name__ == "__main__":
    run_scraper()