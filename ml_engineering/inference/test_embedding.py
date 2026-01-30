import json
from sentence_transformers import SentenceTransformer
import numpy as np

def run_simple_inference():
    # 1. 전처리된 데이터 읽기
    with open("data/processed_gumi_policy.json", "r", encoding="utf-8") as f:
        policy = json.load(f)

    # 2. 한국어 임베딩 모델 로드 (처음 실행 시 다운로드로 인해 시간이 좀 걸립니다)
    # 우리 requirements.txt에 sentence-transformers가 있어야 합니다.
    model = SentenceTransformer('jhgan/ko-sbert-sts')

    # 3. 텍스트를 숫자로 변환 (Vectorization)
    # 제목과 혜택 요약을 합쳐서 모델에 넣습니다.
    text_to_embed = f"{policy['title']} {policy['eligibility']['benefit_summary']}"
    embedding = model.encode(text_to_embed)

    # 4. 결과 확인 (맛보기)
    print(f"✅ 모델 추론 완료!")
    print(f"📍 입력 텍스트: {text_to_embed}")
    print(f"📍 생성된 벡터 차원: {embedding.shape}") # 보통 768차원의 숫자가 나옵니다.
    print(f"📍 벡터 샘플(앞 5개): {embedding[:5]}")

    # 5. 간단한 카테고리 매칭 테스트
    categories = ["주거 및 월세 지원", "일자리 및 취업 지원", "창업 및 사업가 지원", "장학금 및 학자금"]
    category_embeddings = model.encode(categories)
    
    # 코사인 유사도 계산 (가장 비슷한 카테고리 찾기)
    scores = np.dot(category_embeddings, embedding) / (np.linalg.norm(category_embeddings, axis=1) * np.linalg.norm(embedding))
    best_idx = np.argmax(scores)
    
    print(f"🎯 모델의 판단: 이 정책은 '{categories[best_idx]}' 카테고리에 가장 가깝습니다! (유사도: {scores[best_idx]:.2f})")

if __name__ == "__main__":
    run_simple_inference()