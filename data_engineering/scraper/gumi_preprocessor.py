from bs4 import BeautifulSoup
import json
import re
from datetime import datetime

def preprocess_gumi_actual():
    # 1. 파일 읽기 (기존에 사냥한 html)
    with open("data/raw_gumi_policy.html", "r", encoding="utf-8") as f:
        html_content = f.read()

    soup = BeautifulSoup(html_content, "html.parser")

    # --- [1] 제목 및 마감 상태 추출 ---
    subject_span = soup.select_one(".subjectbox > span")
    status_tag = subject_span.find("em", class_="purpose_stat")
    
    # 상태 텍스트 추출 (접수마감)
    status_text = status_tag.get_text(strip=True) if status_tag else "진행중"
    
    # 제목 추출 (상태 태그를 제외한 순수 텍스트)
    if status_tag:
        status_tag.decompose() # em 태그를 제거해서 제목만 남김
    raw_title = subject_span.get_text(strip=True)
    clean_title = re.sub(r"★마감★\(.*?\)", "", raw_title).strip()

    # --- [2] 필드 리스트 추출 (접수기간, 문의처 등) ---
    fields = {}
    for item in soup.select(".field_item"):
        key = item.find("em", class_="title").get_text(strip=True)
        val = item.find("span", class_="text").get_text(strip=True)
        fields[key] = val

    # --- [3] 상세 내용 정밀 파싱 (contenttext) ---
    content_text = soup.find("div", class_="contenttext").get_text("\n", strip=True)
    
    # 정규표현식으로 핵심 정보만 쏙쏙 (데이터 엔지니어링의 정수!)
    benefit_match = re.search(r"지원내용 : (.*?)(?=\n|$)", content_text)
    age_match = re.search(r"지원대상 : (.*?)이하", content_text)
    income_match = re.search(r"\(소득\) (.*?)(?=\n|$)", content_text)

    # --- [4] 기한 필터링 (현재 2026.01.30 기준) ---
    # fields['접수기간']에서 종료일(2025.05.02)을 추출합니다.
    end_date_str = fields.get("접수기간", "2000.01.01").split("~")[1].strip()
    end_date = datetime.strptime(end_date_str, "%Y.%m.%d")
    current_date = datetime(2026, 1, 30)
    
    # 텍스트에 "마감"이 있거나 날짜가 지났으면 비활성
    is_active = (current_date <= end_date) and ("마감" not in status_text)

    # --- [5] 결과 구조화 ---
    processed_data = {
        "title": clean_title,
        "is_active": is_active,
        "status_tag": status_text,
        "metadata": {
            "period": fields.get("접수기간"),
            "contact": fields.get("문의처"),
            "capacity": fields.get("모집인원")
        },
        "eligibility": {
            "age": age_match.group(1).strip() if age_match else "정보없음",
            "income": income_match.group(1).strip() if income_match else "정보없음",
            "benefit_summary": benefit_match.group(1).strip() if benefit_match else "정보없음"
        },
        "apply_method": "온라인/방문 신청 (주소지 관할 담당자 접수)"
    }

    # JSON 저장
    with open("data/processed_gumi_policy.json", "w", encoding="utf-8") as f:
        json.dump(processed_data, f, ensure_ascii=False, indent=4)

    print(f"✅ 정밀 전처리 완료: {clean_title}")
    print(f"📊 현재 상태: {'활성' if is_active else '만료(쓰레기 데이터 거름)'}")

if __name__ == "__main__":
    preprocess_gumi_actual()