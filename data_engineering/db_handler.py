import psycopg2 # PostgreSQL과 소통하기 위한 파이썬 표준 라이브러리
from psycopg2.extras import Json # 파이썬 딕셔너리를 PostgreSQL의 JSONB 형식으로 변환해주는 도구
import logging # 프로그램이 잘 돌아가는지 기록을 남기는 도구

# 로그 설정: 어떤 작업이 일어나는지 터미널에 예쁘게 찍어줍니다.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DBHandler:
    def __init__(self, db_config):
        """
        db_config: host, db이름, 유저, 비번 등이 담긴 설정 딕셔너리
        """
        self.config = db_config # 설정 정보를 클래스 내부에 저장
        self.conn = None # 처음엔 연결된 상태가 아니니까 None으로 시작

    def connect(self):
        """실제로 DB의 문을 두드려 연결하는 함수"""
        try:
            # **self.config: 딕셔너리 내용을 풀어서 connect 함수의 인자로 전달
            self.conn = psycopg2.connect(**self.config)
            logger.info("✅ PostgreSQL 연결 성공!")
            return self.conn
        except Exception as e:
            logger.error(f"❌ DB 연결 실패: {e}")
            raise # 실패하면 프로그램을 멈추고 에러를 알림

    def insert_scraped_data(self, title, content, source_url, metadata):
        """스크래퍼가 긁어온 데이터를 DB 테이블에 집어넣는 함수"""
        
        # 실행할 SQL 쿼리문: %s는 나중에 데이터가 들어갈 구멍(Place Holder)
        # RETURNING id: 데이터를 넣자마자 그 데이터의 PK(번호)를 돌려달라는 뜻
        sql = """
        INSERT INTO scraped_data (title, content, source_url, raw_metadata)
        VALUES (%s, %s, %s, %s)
        RETURNING id;
        """
        
        try:
            # with문을 쓰면 작업이 끝난 뒤 커서(작업용 연필)를 자동으로 반납함
            with self.conn.cursor() as cur:
                # 실제로 쿼리를 실행함. Json(metadata)가 딕셔너리를 JSONB 포맷으로 바꿔줌
                cur.execute(sql, (title, content, source_url, Json(metadata)))
                
                # RETURNING id로 요청했던 생성된 ID 값을 가져옴
                row_id = cur.fetchone()[0]
                
                # 중요! DB에 '진짜로' 저장해달라고 확정 도장(Commit)을 찍음
                self.conn.commit()
                
                logger.info(f"💾 데이터 저장 완료! (DB ID: {row_id})")
                return row_id
        except Exception as e:
            # 작업 중 에러가 나면, 어설프게 저장하지 말고 아예 취소(Rollback)함
            self.conn.rollback()
            logger.error(f"❌ 데이터 저장 실패: {e}")
            raise

    def close(self):
        """다 썼으면 DB와의 연결을 정중하게 끊어주는 함수"""
        if self.conn:
            self.conn.close()
            logger.info("🔌 DB 연결 종료.")