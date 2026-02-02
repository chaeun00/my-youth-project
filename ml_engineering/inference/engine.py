from sentence_transformers import SentenceTransformer
import numpy as np

class PolicyInferenceEngine:
    def __init__(self, model_name='jhgan/ko-sbert-sts'):
        # 1. 모델 로드: 한국어 문장 의미를 이해하는 모델을 메모리에 올립니다.
        self.model = SentenceTransformer(model_name)

        # 2. 기준 카테고리 정의: AI가 분류할 선택지입니다.
        self.categories = [
            "주거 및 월세 지원", 
            "일자리 및 취업 지원", 
            "창업 및 사업가 지원", 
            "장학금 및 학자금"
        ]

        # 3. 카테고리 임베딩(숫자화): 기준이 되는 카테고리명들을 미리 숫자로 바꿔둡니다. 
        # 이렇게 미리 해두면 사용자가 요청할 때마다 계산할 필요가 없어 성능이 빨라집니다.
        self.category_embeddings = self.model.encode(self.categories)

    def predict_category(self, text):

        # A. 입력 텍스트 벡터화: 분석할 문장을 숫자로 변환합니다.
        embedding = self.model.encode(text)

        # B. 코사인 유사도 계산: 
        # 두 벡터(숫자 뭉치) 사이의 각도를 구해 얼마나 비슷한지 측정합니다.
        scores = np.dot(self.category_embeddings, embedding) / (
            np.linalg.norm(self.category_embeddings, axis=1) * np.linalg.norm(embedding)
        )

        # C. 최적의 결과 도출: 유사도 점수가 가장 높은 인덱스를 찾습니다.
        best_idx = np.argmax(scores)
        
        # D. 결과 반환: 가장 높은 카테고리와 점수, 그리고 전체 점수표를 함께 돌려줍니다.
        return {
            "category": self.categories[best_idx],
            "score": round(float(scores[best_idx]), 4),
            "all_scores": {cat: round(float(s), 4) for cat, s in zip(self.categories, scores)}
        }