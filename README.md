# 💰 청년 금융 비서 (Youth Asset Manager)
> **"단순 혜택 추천을 넘어, 자산 형성의 길을 열다"** > XAI 기반 맞춤형 공공/민간 혜택 추천 및 대안 신용평가 기반 자산 포트폴리오 솔루션

![Python](https://img.shields.io/badge/Python-3.9-blue?logo=python)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker)
![FastAPI](https://img.shields.io/badge/FastAPI-0.95-009688?logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?logo=postgresql)
![License](https://img.shields.io/badge/License-MIT-green)

---

## 📖 프로젝트 개요 (Overview)

경제적 자립이 필요한 청년(취준생, 쉬었음 청년, 사회초년생)들에게 **중앙정부 및 지자체의 혜택을 개인화하여 추천**하고, 이를 레버리지로 삼아 **신용 점수 향상 및 자산 형성**까지 돕는 **'AI 금융 비서'** 서비스입니다.

기존 서비스들이 단순한 '조건 필터링'에 그쳤다면, 본 프로젝트는 **XAI(설명 가능한 AI)**를 도입하여 **"왜 이 혜택이 추천되었는지"**, **"아깝게 놓친 혜택(Near-miss)을 받으려면 무엇을 해야 하는지"** 구체적인 행동 가이드를 제시합니다.

### 🚀 핵심 차별점 (Key Differentiators)
1.  **동적 자산 시뮬레이션:** 단순 지원금 표시를 넘어, 지원금 수령 시 변동되는 가처분 소득과 대출/적금 승인 확률을 시뮬레이션하여 제공.
2.  **XAI 기반 근거 제시:** "소득 요건이 충족되어 추천됨", "전입신고 시 수령 가능성 85% 상승" 등 명확한 인과관계 설명.
3.  **Near-Miss 복구 전략:** 아깝게 탈락한 상위 30~40% 승인 확률 상품에 대해 **'임계값(Threshold) 달성 조건'**을 역산하여 가이드 제공.
4.  **대안 신용평가 확장:** 현금/현물 지원 이력을 데이터화하여 금융권의 씬 파일러(Thin Filer) 청년들에게 추가적인 금융 기회 제공.

---

## 🏗️ 시스템 아키텍처 (System Architecture)

본 프로젝트는 **4-Layer Architecture**를 기반으로 하며, 데이터의 수집부터 모델 학습, 서비스 배포까지 **MLOps** 파이프라인으로 연결되어 있습니다.

### 1. Data Engineering Layer (기반)
- **증분 수집 (Incremental Scraping)**: 매번 전체를 긁지 않고 변경된 공고만 수집하여 리소스 최적화 및 변경 이력(Lineage) 추적.

- **Linux CLI Pipeline**: awk, sed 등을 활용한 고성능 텍스트 전처리.

- **수집 주기**: 중앙정부(분기별) / 지자체(주 1~2회) 이원화 전략으로 최신성 유지.

### 2. Intelligence Layer (두뇌)
- **Hybrid Matching**: 사용자 프로필 벡터와 정책 문서 벡터 간의 유사도 매칭.

- **XAI Module**: SHAP/LIME을 활용해 추천 근거를 자연어로 변환.

- **Simulation**: 지원금 수령 시나리오에 따른 미래 자산 변화 예측.

### 3. Service Layer (얼굴)
- **FastAPI**: 비동기 처리를 통한 고성능 API 서버.

- **Interactive Chatbot**: 단순 Q&A가 아닌, 사용자의 상황을 진단하고 솔루션을 제안하는 에이전트.

### 4. MLOps & Governance (척추)
- **Auto-scaling**: 트래픽 급증 시 유연한 확장 (Docker/K8s).

- **CT Pipeline**: 데이터 드리프트 감지 시 모델 자동 재학습 수행.

---

## 📂 디렉토리 구조 (Directory Structure)

```bash
my-youth-project/
├── data/                   # Raw(PDF/JPG) 및 정제된 데이터 저장소 (Git LFS/DVC 권장)
├── data_engineering/       # [Layer 1] 데이터 수집 및 가공
│   ├── scraper/            # Linux Cron/CLI 기반 증분 수집기
│   └── preprocess/         # awk, sed, grep 활용 전처리 스크립트
├── ml_engineering/         # [Layer 2] 모델 학습 및 추론
│   ├── train/              # 모델 학습 및 Fine-tuning 로직
│   └── eval/               # 성능 평가 및 XAI 시각화
├── api_service/            # [Layer 3] FastAPI 기반 백엔드 및 챗봇 로직
├── notebooks/              # 데이터 분석(EDA) 및 프로토타입 실험 (Jupyter)
├── deploy/                 # [Layer 4] 배포 및 인프라 설정
│   ├── docker/             # Dockerfile 모음
│   ├── k8s/                # Kubernetes Manifests (Deployment, Service, Ingress)
│   ├── airflow/            # 데이터 파이프라인 DAGs
│   └── mlflow/             # 실험 관리 설정
├── configs/                # 환경 설정 (DB 연결, API Key 등 - .gitignore 필수)
└── docker-compose.yml      # 로컬 개발 환경 오케스트레이션
```

---

## 요구사항 명세 (Requirements)
### Functional Requirements (기능)
- **FR-1 (Pipeline)**: 지자체/공공데이터 자동 수집-정제-벡터화 파이프라인.

- **FR-2 (Calculation)**: 소득/지역 기반 예상 수령액 원 단위 산출.

- **FR-3 (Simulation)**: 보조금 수령 후 신용점수 및 대출 승인 확률 변화 예측.

- **FR-4 (XAI)**: 추천 결과에 대한 '피처 중요도' 시각화 및 설명.

- **FR-5 (Action Plan)**: 탈락한 혜택의 승인 확률을 높이는 구체적 조건 변경 가이드.

- **FR-6 (Extension)**: 추가 가점 요인(교육 이수 등) 반영 시 혜택 증가분 수치화.

- **FR-7 (Security)**: 간편 인증(OAuth) 및 금융 데이터 암호화.

- **FR-8 (Interface)**: 대화형 지능형 가이드(챗봇) UI.

### Non-Functional Requirements (품질)
- **NFR-1 (CI/CD)**: 무중단 배포(Blue-Green) 환경 구축.

- **NFR-2 (Scalability)**: 동시 접속 1,000명 대응 Auto-scaling (가동률 99.9%).

- **NFR-3 (Latency)**: 최종 추천 응답 속도 3초 이내.

- **NFR-4 (Security)**: DB 암호화 및 망 분리 준수.

- **NFR-5 (Integrity)**: Great Expectations 등을 활용한 데이터 무결성 검증.

- **NFR-6 (CT)**: 데이터 드리프트 감지 및 지속적 학습 파이프라인.

- **NFR-7 (Lifecycle)**: 혜택 종료/신규 데이터의 생애 주기 관리.

---

## 🛠️ 시작하기 (Getting Started)
### Prerequisites
- Windows (WSL2) or Linux

- Docker & Docker Compose

- Python 3.9+

### Installation & Run (Phase 1: MVP)
1. **Repository Clone**
```bash
git clone [https://github.com/your-username/my-youth-project.git](https://github.com/your-username/my-youth-project.git)
cd my-youth-project
```

2. **Environment Setup** `configs/` 폴더 내에 `.env` 파일을 생성하고 필요한 키 값을 입력합니다.
```
cp configs/env.example configs/.env
```

3. **Run with Docker Compose** DB와 API 서버를 실행합니다.
```
docker-compose up -d --build
```

4. **Check Status**

- API Docs: `http://localhost:8000/docs`

- DB Admin: `http://localhost:5050` (pgAdmin)

---

## 📞 Contact
- **Project Lead**: Chaeun Kim

- **Email**: kimchaeun111@gmail.com

- **Github**: https://github.com/chaeun00/