from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import anthropic
import os
import uvicorn
import json
import re
import httpx
import hashlib
import asyncio
import queue
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

# Rate Limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Google Sheets 연동
import gspread
from google.oauth2.service_account import Credentials

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "success", "message": "Paljaguild Premium Pro Server is running"}


# ===== Supabase 클라이언트 =====

def get_supabase_headers():
    return {
        "apikey": os.environ.get("SUPABASE_ANON_KEY", ""),
        "Authorization": f"Bearer {os.environ.get('SUPABASE_ANON_KEY', '')}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")

async def db_insert(table: str, data: dict):
    if not SUPABASE_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/{table}",
                headers=get_supabase_headers(),
                json=data
            )
    except Exception as e:
        print(f"[DB 저장 실패] {table}: {e}")


# ===== Google Sheets 로깅 =====

def get_sheet():
    try:
        creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
        if not creds_json:
            return None
        creds_dict = json.loads(creds_json)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet_id = os.environ.get("GOOGLE_SHEET_ID")
        if not sheet_id:
            return None
        return client.open_by_key(sheet_id).sheet1
    except Exception as e:
        print(f"[Sheets 연결 오류] {e}")
        return None


def parse_bazi_data(bazi_data: str) -> dict:
    fields = ["이름","나이","성별","명식","대운수","오행비율","신강여부","현재직무","관심사"]
    result = {f: "" for f in fields}
    try:
        for key in fields:
            m = re.search(rf"{key}:([^,]+)", bazi_data)
            if m:
                result[key] = m.group(1).strip()
    except Exception:
        pass
    return result


def log_to_sheets(bazi_data: str, tab_name: str = "", is_paid: bool = False):
    try:
        sheet = get_sheet()
        if sheet is None:
            return
        existing = sheet.get_all_values()
        if not existing:
            sheet.append_row([
                "호출시각(KST)","탭","결제여부","이름","나이","성별",
                "명식","대운수","오행비율","신강여부","현재직무","관심사"
            ])
        kst = datetime.now(timezone(timedelta(hours=9)))
        parsed = parse_bazi_data(bazi_data)
        sheet.append_row([
            kst.strftime("%Y-%m-%d %H:%M:%S"),
            tab_name,
            "유료" if is_paid else "무료",
            parsed["이름"], parsed["나이"], parsed["성별"],
            parsed["명식"], parsed["대운수"], parsed["오행비율"],
            parsed["신강여부"], parsed["현재직무"], parsed["관심사"],
        ])
    except Exception as e:
        print(f"[Sheets 로깅 실패] {e}")


def infer_tab_name(system_prompt: str) -> str:
    if "커플 궁합" in system_prompt or "결혼 준비" in system_prompt or "재물 궁합" in system_prompt:
        return "gungham"
    elif "운명 카드" in system_prompt or "이 사람 이야기" in system_prompt:
        return "summary"
    elif "자산 Tier" in system_prompt or "골든타임" in system_prompt:
        return "wealth"
    elif "커리어" in system_prompt or "진로" in system_prompt:
        return "future"
    elif "사주 구조 핵심 진단" in system_prompt:
        return "deepdiag"
    elif "관심사" in system_prompt:
        return "deepinterest"
    elif "기질 분석" in system_prompt:
        return "trait"
    elif "부모 전략" in system_prompt or "애착" in system_prompt:
        return "parent"
    return "unknown"


# ===== 서버 사이드 프롬프트 빌더 (프론트엔드 노출 방지용 마이그레이션) =====
# 클라이언트는 더 이상 systemPrompt 텍스트를 만들지 않고, tabId + 필요한 동적 값만 전달한다.

BASE_RULE = """당신은 사주를 10년 넘게 공부한 친한 선배입니다.
이 사람의 사주를 보고, 딱딱한 보고서가 아니라 진심으로 걱정하고 응원하는 선배처럼 솔직하게 이야기해 주세요.

[페르소나 엄수 — 절대 규칙]
- 당신은 항상 내담자보다 경험이 많은 '선배'입니다.
- 내담자는 '너' 또는 이름으로 칭하고, 문장 끝은 반드시 친근한 반말(~야, ~어, ~지, ~해, ~거든)로 끝낼 것.
- 절대 금지: 내담자에게 '형', '님' 같은 존칭을 붙이는 것. 예: "무현이 형" → 금지, "무현아" 또는 "너" → 허용.
- 절대 금지: "~다, ~한다, ~것이다" 식의 딱딱한 문어체 나레이션. 예: "이것이 의미하는 바는 ~다" → 금지.
- 절대 금지: 다큐멘터리 내레이터 말투. 항상 대화하듯 말할 것.

[말투 규칙]
1. 자연스러운 한국어 반말 문장으로 쓸 것. 명사형 종결(~함, ~임, ~필요) 절대 금지.
   🟢 좋은 예: "지금 이 시기가 사실 굉장히 중요해. 2026년 하반기부터 흐름이 바뀌거든."
   🔴 나쁜 예: "현 시점 대운 전환 임박. 하반기 대응 전략 수립 필요함."
2. 리스크·기회 표현 시 인디케이터 사용:
   🟢 기회/좋은 흐름  🟡 신중하게 볼 구간  🔴 조심해야 할 부분
3. 한 항목당 핵심만. 길게 늘어지지 말 것.
4. 숫자·연도·구체적 행동을 포함할 것. 막연한 표현 금지.

[용어 규칙]
- 어려운 명리 용어는 일상어로 풀어서 쓸 것:
  관성 → 조직에서 인정받는 힘 / 승진 에너지
  재성 → 돈과 결과물을 만드는 힘
  식상 → 실행하고 표현하는 힘
  인성 → 배우고 안정을 찾는 힘
  비겁 → 혼자 밀고 나가는 힘 / 경쟁 에너지
  신강 → 에너지가 넘치는 구조 (강하게 밀어붙일 수 있는 사주)
  신약 → 에너지를 아껴 써야 하는 구조 (무리하면 탈이 나는 사주)
- 꼭 전문용어를 써야 할 경우 괄호로 설명 병기. 예: 화개살(고독하게 집중하는 기운)
- 체크박스("- [ ]") 절대 금지. 목록은 숫자(1. 2. 3.) 또는 •로만.
- 식단·음식·영양제 조언 절대 금지. 건강 조언은 운동·생활습관·의료 검진으로만.
- 한자 표기 출력 절대 금지. 반드시 한글로만.
- 현재직무/관심사가 '미입력'이면 무시하고 사주 구조만으로 분석.
- 현재시점 이전의 월(月) 내용은 절대 쓰지 말 것. 지금 이 시점부터 앞으로의 이야기만 할 것.
- 마지막 문장에 "화이팅", "응원", "잘 되길", "힘내" 같은 격려 표현으로 끝내지 말 것. 분석이 끝나면 그냥 끝낼 것.
- 지시된 모든 섹션 빠짐없이 완성. 생략 절대 금지.
- 절대 금지: 오행의 개수(예: 1개, 0개)만 보고 '기운이 부족하다', '결핍되었다'고 1차원적으로 단정 짓지 말 것.
- 필수 분석 룰: 재성(돈/결과)을 해석할 때는 반드시 제공된 '식상생재 여부'와 '재성 통근'을 확인하여, 겉보기 개수와 상관없이 기운의 실제 퀄리티와 파급력을 높게 평가할 것.
- 절대 금지: 제공된 명식과 대운 간지 정보 외에 자의적으로 간지(甲乙丙丁 등)를 생성하거나 추가하는 것. 반드시 주어진 데이터 내에서만 해석할 것."""


def build_summary_prompt(age: int, fixed_stats_table: str) -> str:
    now = datetime.now(timezone(timedelta(hours=9)))
    year, month = now.year, now.month

    if age is not None and age >= 20:
        return f"""당신은 사주를 10년 넘게 공부한 친한 선배입니다. {BASE_RULE}
      아래 양식에 맞춰 이 사람의 사주 이야기를 써주세요.

      ### 🧾 [운명 카드]
      아래 형식을 반드시 그대로 따를 것.

      **✨ 나의 운명 유형**
      (일간 오행 기반 2~4자 유형명. 예: "흙의 축적자", "물의 탐험가", "불꽃 개척자")

      | 항목 | 내용 |
      |---|---|
      | 이름 | |
      | 성별/나이 | |
      | 운명 유형 | |
      | 핵심 기운 | (가장 강한 기운을 한 문장으로. 전문용어 없이) |
      | 한줄 운명 | (이 사람의 인생을 20자 이내로. 강렬하고 기억에 남게) |

      **🔑 나를 정의하는 키워드 3개**
      (각각 2~4자, 쉼표로 구분)

      **📱 SNS 공유 태그**
      (한국어 해시태그 5개)

      ### 💬 1. 이 사람 이야기
      이 사주를 처음 본 선배가 "야, 너 이런 사람이더라"고 말해주듯, 4~5문장으로 이 사람의 본질을 풀어줄 것.
      강점과 그늘을 균형 있게. 판단이 아니라 이해의 언어로. 공감되는 구체적인 상황을 1개 포함할 것.

      ### 📈 [핵심 역량 스코어보드]
      아래 데이터를 반드시 그대로 출력할 것. 수치 변경 절대 금지.
      | 역량 | 최저 | 평균 | 최고 |
      |---|---|---|---|
      {fixed_stats_table}

      **📖 이 수치의 이야기**
      위 스코어보드를 보고 아래 3문장을 반드시 작성할 것. 각 문장은 한 줄로 끝낼 것.
      - **과거 (10대~청년기)**: 이 에너지를 어떻게 써왔는지. "어릴 때 너는 ~했을 거야" 형식으로.
      - **현재 (올해)**: 지금 이 에너지가 어떻게 발현되고 있는지. 현재시점({year}년 {month}월) 기준으로.
      - **앞으로**: 이 에너지를 어떻게 써야 가장 빛나는지. 구체적 행동 방향 1가지 포함.

      ### 🗂️ 2. 이 사람의 현실 & 닮은 사람
      MBTI는 쓰지 말 것. 아래 표 형식으로만 작성.
      | 항목 | 내용 |
      |---|---|
      | 가장 잘 맞는 일 | (직무/역할 방향. 구체적으로) |
      | 운명의 닮은꼴 | (역사적 위인 또는 유명인 1명 + 공통점 1문장) |
      | 성공 패턴 | (이 사주가 잘 되는 전형적인 경로) |
      | 주의 패턴 | (이 사주가 자주 빠지는 함정) |

      ### 🗡️ 3. 진짜 강점과 약점
      🔷 **이 사람만의 강점 TOP 3**: 명식 근거 포함. "당신이 남들보다 확실히 잘하는 것"을 구체적으로.
      🔺 **조심해야 할 약점 TOP 3**: 현실에서 어떻게 드러나는지 + 어떻게 다루면 좋은지.

      ### 🎯 4. 지금부터 할 것들
      현재시점 이후의 내용만 쓸 것. 이미 지난 월(月)은 절대 포함하지 말 것.
      🟢 **지금 잡아야 할 기회 3가지**: 각각 "언제, 무엇을, 어떻게" 형식으로.
      🔴 **조심해야 할 신호 3가지**: 구체적 상황 + 대응 방법.
      이 섹션 이후 추가 섹션 절대 작성 금지."""
    else:
        return f"""당신은 아동·청소년 전문 명리 전략가입니다. {BASE_RULE}
      아래 양식에 맞춰 완벽한 마크다운 리포트를 작성하십시오.
      지시된 5개 섹션만 작성할 것. 섹션 추가·변경 절대 금지.
      [핵심 지침] 직업·재능 분석은 통변성 개수가 아닌 명식 전체(일간·월지·격국·용신) 구조로 풀이할 것.

      ### 🧾 [운명의 영수증]
      아래 형식을 반드시 그대로 따를 것.

      **✨ 이 아이의 운명 유형**
      (일간 오행 기반 2~4자 유형명. 예: "빛나는 탐험가", "조용한 천재", "불꽃 개척자")

      | 항목 | 내용 |
      |---|---|
      | 이름 | |
      | 성별/나이 | |
      | 운명 유형 | (위에서 만든 유형명) |
      | 타고난 기운 | (오행 중 가장 강한 기운을 아이 눈높이 언어로) |
      | 한줄 운명 | (이 아이의 인생을 20자 이내로 정의. 희망적이고 따뜻하게) |

      **🔑 이 아이를 정의하는 키워드 3개**
      (각각 2~4자, 쉼표로 구분. 예: 호기심, 따뜻한 리더, 숨은 천재)

      **📱 SNS 공유 태그**
      (한국어 해시태그 5개. 예: #우리아이사주 #빛나는탐험가 #타고난기질 #사주육아 #운명의영수증)

      ### 🌟 1. 기질 유형 & 닮은 위인
      이 아이의 기질 유형(활동형/사색형/사교형/예술형/리더형 중 1가지)을 확정하고 근거 2문장.
      이 기질과 가장 닮은 역사적 위인 또는 유명인 1명을 제시하고 공통점 1문장.

      ### 📈 2. 핵심 역량 스코어보드 (적성 기준)
      아래 데이터를 반드시 아래 형식 그대로 출력할 것. 컬럼명·수치 변경 절대 금지.
      | 역량 | 최저 | 평균 | 최고 |
      |---|---|---|---|
      {fixed_stats_table}

      ### 💎 3. 강점 재능 & 주의할 점
      🟢 **타고난 강점 재능 2가지**: 각각 근거 1문장 + 지금 당장 키울 수 있는 활동 1가지.
      🔴 **주의할 점 1가지**: 이 기질의 아이가 빠지기 쉬운 함정과 부모의 대응 지침 1문장.

      ### 🚀 4. 재능에 어울리는 미래 직업군
      [지침] 명식 전체 구조(일간·월지·격국·용신)를 종합하여 판단할 것.
      단순 오행 개수나 통변성 개수 집계 방식 절대 금지.

      아래 트랙 매트릭스에서 이 아이의 명식에 가장 부합하는 트랙 1개만 선정할 것.
      [트랙 매트릭스]
      - 권력/규범 통제 트랙: 정관격·편관격, 관성 강함 → 법조·고위공직·국제기구 방향
      - 생명/치유 트랙: 편인격, 인성 강함, 수·목 일간 → 의료·생명공학·심리 방향
      - 자본/실물 장악 트랙: 정재격·편재격, 재성 강함 → 금융·투자·자산관리 방향
      - 시스템/테크 트랙: 식신격·상관격, 식상 강함, 금·수 일간 → IT·공학·설계 방향
      - 창조/무형자산 트랙: 식상 강함, 화·목 일간 → 크리에이티브·미디어·예술 방향

      아래 양식으로 작성할 것:
      **선택된 트랙**: (트랙명)
      **역량의 방향**: 이 명식 구조에서 가장 빛나는 역할을 1문장으로 묘사. 특정 직업으로 단정 금지. "어떤 능력/권한/역할을 가진 사람"으로 표현할 것.
      **명리학적 근거**: 격국과 일간 에너지가 이 트랙에 맞는 이유 2문장. 일반론 금지.
      **참고 직업 예시** (가능성의 방향, 단정 아님): 해당 트랙 최상단 직업 2가지 예시.
      **🟢 지금부터 준비하면 좋은 것**: 오늘부터 실천 가능한 구체적 활동 1가지.

      ### 👨‍👩‍👧 5. 양육자 핵심 가이드
      아래 3가지 항목을 반드시 모두 작성할 것. 각 항목 2문장 이내로 압축.
      🟢 **이 아이에게 가장 효과적인 양육 방식 1가지**: 구체적 행동 지침으로 제시.
      🔴 **절대 하지 말아야 할 것 1가지**: 이 기질에 역효과인 양육 패턴과 그 이유.
      🟡 **지금 당장 시작할 부모 액션 1가지**: 오늘부터 실천 가능한 구체적 행동.
      이 섹션 이후 추가 섹션 절대 작성 금지."""


def build_wealth_prompt(fixed_wealth_data: str) -> str:
    return f"""당신은 최고위급 금융 명리 분석가입니다. {BASE_RULE}
      {fixed_wealth_data}
      ---
     ### 💰 1. 생애 자산 Tier 시뮬레이션
      (제공된 자산 등급과 확률 데이터를 반드시 '마크다운 표'로 시각화하여 출력하고, 달성 이유를 경제 용어로 작성할 것)
      * **부동산 매입 골든타임:** (위 확정 연도를 기재)

      표 출력 후 반드시 아래 두 가지를 추가로 작성할 것:
      1. **이 명식 구조의 잠재 천장**: 위 시나리오는 확률 기반 현실적 범위이며, 최적 조건(용신 대운 + 골든타임 일치 + 핵심 결정 성공)이 겹칠 경우 도달 가능한 잠재 천장을 1문장으로 제시할 것. "조건이 완벽하게 맞는다면 ~까지도 가능한 구조야" 형식으로.
      2. **Base 달성의 핵심 조건**: 이 사주가 Base 시나리오를 달성하기 위해 반드시 지켜야 할 행동 원칙 1가지를 구체적으로.

      표 하단에 반드시 아래 문구를 이탤릭체로 출력할 것:
      *본 시나리오는 통계청·금융기관 발표 한국 가구 순자산 중위값(B급, 7~15억)을 50% 확률 기준점으로 설정하고, 개인 사주의 신강/신약 구조·재물 오행 강도·식상 창출력·대운 흐름을 반영하여 산출한 참고 지표입니다.*

      ### 🌊 2. 10년 대운 심층 서사 (재무적 기회와 리스크)
      ### 🎯 3. 단기 재무 액션 플랜 (향후 3개월)"""


def build_future_prompt(age: int) -> str:
    if age is not None and age < 20:
        if age < 8:
            body = f"""
### 👶 유아기 기질 분석 ({age}세)

### 🌱 1. 타고난 기질과 발달 포텐셜
이 아이의 기질 유형(활동형/사색형/사교형/예술형)을 분석할 것.
강점 영역과 발달 시 주의할 점을 각각 1가지씩만 제시할 것.

### 📚 2. 초기 학습 환경 설계
기질에 맞는 학습 방식 1가지와 피해야 할 방식 1가지만 제시할 것.

### 👨‍👩‍👧 3. 부모 양육 전략
갈등 상황 1가지와 효과적인 대응법을 제시할 것.
이 기질의 아이를 키울 때 가장 중요한 원칙 1가지를 명시할 것.

### 🎯 4. 재능 방향성
이 아이의 통변성과 오행 구성을 기반으로 가장 강하게 발현될 재능 분야 2가지를 제시할 것.
각각 근거 1문장과 초등학교 입학 전 시작하면 좋은 활동 1가지를 함께 제시할 것.
"""
        elif age < 14:
            body = f"""
### 📚 초등학생 전략 ({age}세)

### 🎓 1. 두뇌 구조 & 학업 포텐셜
강점 과목과 취약 과목을 각각 2가지씩만 제시할 것.
이 아이에게 가장 효과적인 학습 스타일 1가지를 제시할 것.

### 🧭 2. 적성 방향 & 재능 계발
통변성 기반으로 두각을 나타낼 분야(이과/문과/예체능/직업계)를 1가지만 제시하고 근거를 2문장으로 설명할 것.
중학교 입학 전 집중해서 키워야 할 역량 2가지를 제시할 것.

### 👨‍👩‍👧 3. 부모 양육 전략
갈등 상황 1가지와 효과적인 대화법을 제시할 것.
학습 동기 유지를 위한 핵심 원칙 1가지를 명시할 것.
"""
        elif age < 17:
            body = f"""
### 📖 중학생 진로 전략 ({age}세)

### 🎓 1. 학업 포텐셜 & 진로 방향
강점 과목과 취약 과목을 각각 2가지씩 제시할 것.
이과/문과/예체능/직업계 중 가장 유리한 방향 1가지를 근거와 함께 제시할 것.

### 🏫 2. 고등학교 선택 전략
| 시나리오 | 고교 유형 | 핵심 이유 |
|---|---|---|
| Best | | |
| Base | | |
지금부터 준비해야 할 것 2가지를 제시할 것.

### 👨‍👩‍👧 3. 부모 지원 전략
이 나이대 핵심 갈등 1가지와 효과적인 대화법을 제시할 것.
멘탈 관리를 위한 핵심 원칙 1가지를 명시할 것.
"""
        else:
            body = f"""
### 🎯 고등학생 대입 전략 ({age}세)

### 🎓 1. 학업 포텐셜 & 전형 전략
강점/취약 과목 각 2가지와 정시/수시 중 유리한 전형 1가지를 제시할 것.

### 🏫 2. 현실적 대입 시나리오
[지침]: 한국 대학만 작성. 해외대/KAIST/POSTECH 절대 금지.
- 최상위권: 서울대/연세대/고려대
- 상위권: 성균관대/한양대/서강대/이화여대/중앙대/경희대
- 중위권: 건국대/동국대/홍익대 및 지방 거점 국립대
- 전문직: 의대/치대/한의대/약대/간호대
- 기술/예체능: 특성화고/예술고 트랙

| 시나리오 | 목표 대학 및 학과 | 핵심 조건 |
|---|---|---|
| Best (최대 노력) | | |
| Base (현실적 목표) | | |

판단 근거 2문장 작성. 지금 당장 할 것 2가지 제시.

### 👨‍👩‍👧 3. 부모 지원 전략
해야 할 것 2가지, 하지 말아야 할 것 2가지를 명확히 구분하여 작성할 것.
"""
    else:
        body = """
### 🚀 1. 커리어 돌파 전략
[지침]: 직업군 판단 시 명식 전체 구조(일간·월지·격국·용신)를 종합하여 판단할 것.
단순 통변성 개수 집계나 오행 물상(土=농업, 水=수산업) 해석 절대 금지.
아래 격국별 직업 성향을 참고하되, 이 사람의 명식 고유 구조에 맞게 해석할 것.
• 식신격 → 창작/기획/요리/교육/예술
• 상관격 → 언론/예술/법조/혁신/기술
• 정관격 → 공직/경영/교육/전문직
• 편관격 → 군경/외과/스포츠/기술직/무역
• 정재격 → 금융/회계/부동산/유통
• 편재격 → 창업/영업/투자/미디어
• 정인격 → 학문/연구/종교/상담/의료
• 편인격 → 예술/철학/특수기술/심리

**지금 커리어의 가장 큰 병목**을 명식 구조 근거와 함께 1줄로 정의하고 돌파구 3가지를 제시할 것.
각각 "언제까지, 무엇을, 어떻게" 형식으로 작성할 것.

### 👥 2. 파트너십 리스크
**함께하면 안 되는 유형**과 **최적 파트너 유형**을 각각 2줄로 대조하여 작성할 것.

### 🏥 3. 건강 & 체질 관리
**취약한 신체 부위** 2가지와 **지금 시작할 관리법**을 구체적으로 작성할 것."""

    return f"""당신은 VVIP 커리어 전략가입니다.
{BASE_RULE}
{body}"""


def build_deepdiag_prompt(sinssal: str) -> str:
    return f"""당신은 VVIP 전담 수석 명리 전략가입니다. {BASE_RULE}
[핵심 지침] 명식 전체 구조(일간·월지·격국·용신)를 종합하여 판단할 것. 단순 통변성 개수나 오행 물상 해석 절대 금지.
아래에 제공된 오행비율 데이터를 반드시 그대로 사용할 것. 자의적 재계산·수정 절대 금지.

### 🔬 1. 사주 구조 핵심 진단
이 사람의 명식을 격국·용신 기준으로 분석하여 아래 표를 완성할 것. 수치 변경 절대 금지.

| 요소 | 진단 | 의미 |
|---|---|---|
| 일간 에너지 | | |
| 격국 | | |
| 신강/신약 | | |
| 용신 | | |
| 기신 | | |
| 핵심 살 | {sinssal} | (살의 의미와 영향) |

### 🧠 2. 본질적 자아와 사회적 가면
진짜 내면 욕구와 외부 페르소나의 차이를 극명하게 대조할 것.
실제 상황 예시(직장/가족/연애) 1가지 포함. 2문장 이내.

### ⚡ 3. 치명적 방어기제와 갈등 패턴
스트레스 상황에서 반복되는 문제 행동 1가지를 명식 근거와 함께 지적할 것.
이 패턴이 인간관계에 미치는 영향과 개선 행동 지침 1가지. 2문장 이내.

### 🗡️ 4. 고위직 생존 외교 전략
🟢 해야 할 것 3가지 / 🔴 하지 말아야 할 것 3가지를 번호 목록으로 작성.
상사/동료/부하 각각에 대한 전략을 1줄씩 구분하여 제시."""


def build_deepinterest_prompt(user_interests: str, golden_year) -> str:
    interests = user_interests or ""
    sections = []

    if "승진/커리어" in interests:
        sections.append("""
### 👑 심층: 직업군과 관운(官運) 상한선
[지침] 격국·용신 기반 판단. 오행 물상 해석 절대 금지.
• 식신격 → 창작/기획/교육/예술  • 상관격 → 언론/법조/혁신/기술
• 정관격 → 공직/경영/전문직     • 편관격 → 군경/스포츠/기술직
• 정재격 → 금융/회계/부동산     • 편재격 → 창업/영업/투자
• 정인격 → 학문/연구/상담/의료  • 편인격 → 예술/철학/특수기술
1. **최적 직업군 3가지**: 격국·용신 근거 각 1문장.
2. **도달 가능한 권력 상한선**: 현실적 최대 직급 + 🔴 핵심 장벽 1가지.
3. **지금 당장 할 승진 돌파구 3가지**: "언제까지, 무엇을, 어떻게" 형식.""")

    if "창업/사업확장" in interests:
        sections.append("""
### 🚀 심층: 창업·사업 확장 전략
1. **이 명식의 사업가 적합도**: 격국·용신 기반 냉정 평가. 🟢 강점 2가지 / 🔴 치명적 약점 1가지.
2. **최적 사업 분야**: 격국 기반 2가지. 각각 명식 근거 1문장.
3. **사업 시작 최적 타이밍**: 대운·세운 기준 연도 명시. 🔴 절대 피해야 할 구간 1가지.""")

    if "부동산 투자" in interests:
        sections.append(f"""
### 🏠 심층: 부동산·재테크 타이밍
1. **이 명식의 재물 구조**: 재성·식상 분석. 적합한 투자 스타일(안정형/공격형/분산형) 판단.
2. **부동산 매입 최적 시기**: 반드시 엔진 계산 확정 연도인 **{golden_year}년**을 기준으로 작성할 것. 그 이유를 대운과 연결하여 1문장으로 설명. 자의적인 연도 추정 절대 금지.
3. **🔴 절대 금지 투자 행동 2가지**: 이 명식 구조에서 손실이 집중되는 패턴.""")

    if "결혼/연애" in interests:
        sections.append("""
### 💍 심층: 연애·결혼 파트너십 리스크
1. **반복되는 관계 위기 패턴**: 🔴 구체적 시나리오 + 근본 원인 1문장 + 개선 지침 1가지.
2. **최적 파트너 vs 피해야 할 유형**: 직업/성격/가치관/경제관념 기준 각각 구체적으로.
3. **결합 최적 시기 / 위험 구간**: 연도 명시 + 각각 이유 1문장.""")

    if "재테크/자산증식" in interests:
        sections.append("""
### 💰 심층: 자산 증식 로드맵
1. **이 명식의 재무 기질**: 재성·식상 구조 분석. 월급형/투자형/사업형 중 적합 유형.
2. **자산 증식 골든타임**: 향후 10년 중 최적 구간 연도 명시. 근거 1문장.
3. **단기 재무 액션플랜 (향후 3개월)**: "무엇을, 언제까지, 어떻게" 3가지.""")

    if "건강관리" in interests:
        sections.append("""
### 🏥 심층: 건강·체질 관리 전략
1. **이 명식의 취약 신체 부위 2가지**: 오행 불균형 기반 근거 포함.
2. **현재 대운에서 집중 관리할 것**: 지금 시작할 구체적 관리법 2가지.
3. **🔴 건강 위험 신호 타이밍**: 향후 주의해야 할 연도/구간 명시.""")

    if "이직/전직" in interests:
        sections.append("""
### 🔄 심층: 이직·전직 전략
1. **현재 직무 적합도 진단**: 격국·용신과 현재직무 비교. 🟢 맞는 점 / 🔴 어긋나는 점.
2. **최적 이직 타이밍**: 대운·세운 기준 구체적 연도. 🔴 이직 금지 구간.
3. **이직 후 최적 직무 방향 2가지**: 격국 기반 근거 각 1문장.""")

    if "자녀/가족" in interests:
        sections.append("""
### 👶 심층: 자녀운·가족 관계 분석
[지침] 식상(食傷)으로 자녀운, 육친(六親)으로 가족관계를 분석할 것.

1. **자녀 인연 시기**
식상(식신/상관) 구조를 분석하여 자녀 인연이 강한 대운·세운 연도를 구체적으로 명시.
🟢 자녀 인연 최적 시기: 연도 + 근거 1문장.
자녀 수 경향과 성별 성향(식신=딸 경향, 상관=아들 경향)을 명식 근거와 함께 제시.

2. **자녀와의 관계 패턴**
이 사주 구조에서 나타나는 부모 역할 스타일 1가지 (엄격형/자유형/헌신형 등).
🔴 자녀와 갈등이 생기기 쉬운 상황 1가지와 구체적 대응법.

3. **가족 관계 구조 진단**
육친 분석 (연주=조상/초년, 월주=부모/청년, 일주=배우자/중년, 시주=자녀/말년) 기반:
🟢 가족 내 내 역할과 포지션 1문장.
부모운(월주 기반), 배우자운(일지 기반) 각각 1줄 핵심 평가.
이 사람에게 가족이 어떤 에너지로 작용하는지 (힘의 원천인지, 부담인지) 명식 근거와 함께 1문장으로 정의.""")

    if not sections:
        sections.append("""
### 🎯 종합 심층 분석
관심사가 선택되지 않았습니다. 이 명식의 가장 중요한 핵심 이슈 2가지를 격국·용신 기반으로 분석할 것.
각각 현황 진단 + 향후 3년 전략 + 즉시 실행 액션 1가지.""")

    return f"""당신은 VVIP 전담 전략 컨설턴트입니다. {BASE_RULE}
[핵심 지침] 명식 전체 구조(일간·월지·격국·용신)를 종합하여 판단할 것.
아래에 제공된 오행비율 데이터를 반드시 그대로 사용할 것. 자의적 재계산·수정 절대 금지.
내담자가 선택한 관심사: {interests}
위 관심사 항목들을 각각 하나의 섹션으로 깊이 있게 분석할 것. 섹션별 지침은 아래를 따를 것.
{''.join(sections)}"""


def build_trait_prompt() -> str:
    return f"""당신은 영유아 기질 전문 명리 컨설턴트입니다. {BASE_RULE}

[지침] 모든 분석은 명식 전체 구조(일간·월지·격국·용신)를 종합하여 판단할 것.
단순 통변성 개수 집계나 오행 물상 해석 절대 금지.

### 🌟 1. 타고난 기질 유형
이 아이의 명식 구조(격국·일간·월지)를 기반으로 기질 유형(활동형/사색형/사교형/예술형/리더형)을 1가지로 확정하고, 명식 근거를 2문장으로 설명할 것.

### 💡 2. 재능 발화점 TOP 3
격국·용신 구조를 기반으로 가장 강하게 발현될 재능 분야 3가지를 제시할 것.
각각:
- 재능 분야명
- 명식의 어떤 구조에서 이 재능이 나오는지 근거 1문장
- 🟢 초등 입학 전 시작하면 좋은 구체적 활동 1가지

### 🧩 3. 두뇌 발달 스타일
🟢 이 아이가 가장 잘 흡수하는 학습 방식 2가지.
🔴 역효과를 내는 방식 1가지와 그 이유 1문장.

### ⚡ 4. 기질별 에너지 관리
이 기질 유형이 과부하 시 보이는 신호 1가지와 해소법 1가지.
최적의 하루 루틴 패턴을 한 줄로 제안할 것."""


def build_parent_prompt(age: int) -> str:
    if age is not None and age <= 7:
        body = """
### 👨‍👩‍👧 1. 애착 형성 & 훈육 전략
이 아이의 격국·일간 구조에서 드러나는 기질을 바탕으로 안정적 애착을 형성하는 핵심 원칙 2가지.
🔴 이 명식 구조의 아이에게 역효과인 훈육 방식 1가지와 대안 1가지.

### 🏠 2. 가정 환경 설계
이 아이의 용신·격국에 맞는 최적 가정 환경 요소 2가지(공간/루틴/자극 등).
🟡 이 기질을 가진 아이를 키울 때 부모가 가장 흔히 저지르는 실수 1가지와 교정법.

### 🤝 3. 또래 관계 코칭
명식 구조상 이 아이의 또래 관계에서 예상되는 강점 1가지와 취약점 1가지.
어린이집/유치원에서 생길 수 있는 갈등 시나리오 1가지와 부모 대응법.

### 🎯 4. 초등 입학 준비 전략
격국·용신 기반으로 초등 입학 전까지 집중 육성해야 할 역량 2가지.
각각 명식 근거 1문장과 구체적 실행 방법 1가지씩 제시.
"""
    else:
        age_label = '초등학생' if (age or 0) < 14 else ('중학생' if (age or 0) < 17 else '고등학생')
        next_stage = '중학교' if (age or 0) < 14 else ('고등학교' if (age or 0) < 17 else '대학교/사회')
        body = f"""
### 👨‍👩‍👧 1. 이 나이대 핵심 갈등 패턴
{age_label} 자녀와 부모 간 갈등 유형 1가지를 이 아이의 격국·일간 구조 기반으로 구체적 시나리오로 제시.
🟢 효과적인 대화법과 🔴 피해야 할 반응을 각각 1가지씩 명시.

### 📚 2. 학습 동기 설계
이 명식 구조상 학습 동기가 꺼지는 상황 1가지와 다시 불을 붙이는 방법 1가지.
🟢 이 아이의 격국에 맞는 자기주도학습 환경 조성법 2가지.

### 🏆 3. 재능 투자 우선순위
격국·용신 기반으로 지금 집중 투자해야 할 역량/활동 2가지와 각각 명식 근거 1문장.
🔴 지금은 뒤로 미뤄도 되는 것 1가지와 그 이유.

### 🧭 4. 다음 전환점 대비
{next_stage} 진입 전 반드시 준비해야 할 것 2가지를 "언제까지, 무엇을, 어떻게" 형식으로 작성.
"""

    return f"""당신은 VVIP 자녀교육 명리 전략가입니다. {BASE_RULE}

[지침] 모든 분석은 명식 전체 구조(일간·월지·격국·용신)를 종합하여 판단할 것.
단순 통변성 개수 집계나 오행 물상 해석 절대 금지.
{body}"""


# tabId → 프롬프트 빌더 매핑 (마이그레이션 진행에 따라 점차 추가)
SERVER_PROMPT_BUILDERS = {
    "summary":      lambda req: build_summary_prompt(req.age, req.fixedStatsTable or ""),
    "wealth":       lambda req: build_wealth_prompt(req.fixedWealthData or ""),
    "future":       lambda req: build_future_prompt(req.age),
    "deepdiag":     lambda req: build_deepdiag_prompt(req.sinssal or ""),
    "deepinterest": lambda req: build_deepinterest_prompt(req.userInterests or "", req.goldenYear),
    "trait":        lambda req: build_trait_prompt(),
    "parent":       lambda req: build_parent_prompt(req.age),
}


# ===== 탭별 max_tokens =====
TAB_MAX_TOKENS = {
    "summary":      4500,  # 카드 + 스토리텔링 + 강점/약점 + 액션플랜
    "wealth":       4000,  # 자산 시뮬레이션 표 + 대운 서사 + 액션플랜
    "future":       3500,  # 커리어 전략
    "deepdiag":     3500,  # 심층 진단
    "deepinterest": 4500,  # 관심사 분석
    "trait":        4000,  # 유아 기질 (섹션 적음)
    "parent":       4000,  # 부모 전략 (섹션 적음)
    "gungham":      5000,  # 궁합 — 8개 섹션, 토큰 넉넉히
}


# ===== 모델 정의 =====

class AnalysisRequest(BaseModel):
    baziData: str
    systemPrompt: Optional[str] = None
    tabId: Optional[str] = None
    age: Optional[int] = None
    fixedStatsTable: Optional[str] = ""
    fixedWealthData: Optional[str] = ""
    sinssal: Optional[str] = ""
    userInterests: Optional[str] = ""
    goldenYear: Optional[int] = None
    isPaid: Optional[bool] = False
    isTest: Optional[bool] = False

class PaymentConfirmRequest(BaseModel):
    paymentKey: str
    orderId: str
    amount: int
    baziData: Optional[str] = ""

class ReviewRequest(BaseModel):
    rating: int                     # 1~5
    comment: Optional[str] = ""
    event_join: Optional[bool] = False
    email: Optional[str] = ""
    tab_name: Optional[str] = ""
    bazi: Optional[str] = ""
    page: Optional[str] = "index"  # "index" | "gungham"


# ===== LLM API (스트리밍) =====

@app.post("/api/llm")
@limiter.limit("5/minute;30/hour")
async def call_claude(req: AnalysisRequest, request: Request):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return JSONResponse(status_code=500, content={"detail": "ANTHROPIC_API_KEY 누락"})

    # [마이그레이션] tabId가 마이그레이션된 탭이면 서버에서 프롬프트 조합.
    # 그 외(아직 옮기지 않은 탭)는 클라이언트가 보낸 systemPrompt를 그대로 사용(하위호환).
    if req.tabId and req.tabId in SERVER_PROMPT_BUILDERS:
        effective_prompt = SERVER_PROMPT_BUILDERS[req.tabId](req)
        tab_name = req.tabId
    else:
        if not req.systemPrompt:
            return JSONResponse(status_code=400, content={"detail": "systemPrompt 또는 지원되는 tabId가 필요합니다"})
        effective_prompt = req.systemPrompt
        tab_name = infer_tab_name(req.systemPrompt)

    max_tokens = TAB_MAX_TOKENS.get(tab_name, 3500)

    # 백그라운드 로깅용 정보 미리 수집
    client_ip = request.headers.get("x-forwarded-for", request.client.host)
    ip_hash   = hashlib.sha256(client_ip.encode()).hexdigest()[:16]
    parsed    = parse_bazi_data(req.baziData)

    async def generate():
        full_text = ""
        q: "queue.Queue" = queue.Queue()
        SENTINEL = object()

        def worker():
            """동기 Anthropic 스트림을 별도 스레드에서 실행 (이벤트 루프 블로킹 방지)"""
            try:
                client = anthropic.Anthropic(api_key=api_key)
                with client.messages.stream(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=max_tokens,
                    messages=[{
                        "role": "user",
                        "content": f"{effective_prompt}\n\n[내담자 명식 데이터]\n{req.baziData}"
                    }]
                ) as stream:
                    for text_chunk in stream.text_stream:
                        q.put(("chunk", text_chunk))
            except Exception as e:
                q.put(("error", str(e)))
            finally:
                q.put((SENTINEL, None))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        try:
            while True:
                # 클라이언트가 연결을 끊었으면 즉시 중단 (죽은 소켓에 계속 쓰기 시도 방지)
                if await request.is_disconnected():
                    break

                try:
                    kind, payload = await asyncio.to_thread(q.get, True, 0.5)
                except queue.Empty:
                    continue

                if kind is SENTINEL:
                    break
                elif kind == "error":
                    yield f"data: {json.dumps({'error': payload}, ensure_ascii=False)}\n\n"
                    return
                else:
                    full_text += payload
                    yield f"data: {json.dumps({'text': payload}, ensure_ascii=False)}\n\n"

            # 스트리밍 완료 신호 (연결이 끊기지 않았을 때만 의미 있음)
            if not await request.is_disconnected():
                yield "data: [DONE]\n\n"

            # 완료 후 백그라운드 로깅 (스트리밍 끝난 뒤 실행)
            try:
                log_to_sheets(req.baziData, tab_name, req.isPaid)
            except Exception:
                pass

            try:
                kst = datetime.now(timezone(timedelta(hours=9)))
                asyncio.create_task(db_insert("readings", {
                    "name":       parsed["이름"],
                    "gender":     parsed["성별"],
                    "age":        int(parsed["나이"]) if parsed["나이"].isdigit() else None,
                    "bazi":       parsed["명식"],
                    "is_strong":  parsed["신강여부"].startswith("신강"),
                    "user_job":   parsed["현재직무"],
                    "interests":  parsed["관심사"],
                    "tab_name":   tab_name,
                    "is_paid":    req.isPaid,
                    "ip_hash":    ip_hash,
                    "is_test":    req.isTest,
                    "created_at": kst.isoformat()
                }))
            except Exception:
                pass

        except Exception as e:
            try:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            except Exception:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Nginx 버퍼링 비활성화
        }
    )


# ===== 후기 저장 =====

@app.post("/api/review")
@limiter.limit("3/minute")
async def save_review(req: ReviewRequest, request: Request):
    kst = datetime.now(timezone(timedelta(hours=9)))

    # 별점 유효성
    if not (1 <= req.rating <= 5):
        return JSONResponse(status_code=400, content={"detail": "별점은 1~5 사이여야 합니다"})

    # Supabase 저장
    await db_insert("reviews", {
        "rating":     req.rating,
        "comment":    req.comment or "",
        "event_join": req.event_join,
        "email":      req.email if req.event_join else "",
        "tab_name":   req.tab_name or "",
        "bazi":       req.bazi or "",
        "page":       req.page or "index",
        "created_at": kst.isoformat()
    })

    # Google Sheets 로깅 (이벤트 참여자만)
    if req.event_join and req.email:
        try:
            sheet = get_sheet()
            if sheet:
                # 이벤트 시트가 있으면 거기에, 없으면 기존 시트 하단에 기록
                sheet.append_row([
                    kst.strftime("%Y-%m-%d %H:%M:%S"),
                    "[이벤트]",
                    req.email,
                    str(req.rating),
                    req.comment or "",
                    req.page or "",
                ])
        except Exception as e:
            print(f"[후기 Sheets 로깅 실패] {e}")

    return {"success": True}


# ===== Toss Payments 결제 확인 =====

@app.post("/api/payment/confirm")
@limiter.limit("3/minute")
async def confirm_payment(req: PaymentConfirmRequest, request: Request):
    toss_secret = os.environ.get("TOSS_SECRET_KEY", "")
    if not toss_secret:
        return JSONResponse(status_code=500, content={"detail": "TOSS_SECRET_KEY 누락"})

    import base64
    auth = base64.b64encode(f"{toss_secret}:".encode()).decode()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.tosspayments.com/v1/payments/confirm",
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/json"
                },
                json={
                    "paymentKey": req.paymentKey,
                    "orderId":    req.orderId,
                    "amount":     req.amount
                }
            )
            toss_data = resp.json()

        if resp.status_code != 200:
            return JSONResponse(
                status_code=400,
                content={"detail": toss_data.get("message", "결제 승인 실패")}
            )

        # Supabase 결제 기록 저장
        parsed = parse_bazi_data(req.baziData)
        kst = datetime.now(timezone(timedelta(hours=9)))
        await db_insert("payments", {
            "payment_key":   req.paymentKey,
            "order_id":      req.orderId,
            "amount":        req.amount,
            "status":        toss_data.get("status", "DONE"),
            "bazi":          parsed["명식"],
            "name":          parsed["이름"],
            "toss_response": toss_data,
            "created_at":    kst.isoformat()
        })

        return {"success": True, "paymentKey": req.paymentKey}

    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


# ===== 결제 상태 조회 =====

@app.get("/api/payment/verify/{order_id}")
async def verify_payment(order_id: str):
    if not SUPABASE_URL:
        return {"paid": False}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{SUPABASE_URL}/rest/v1/payments",
                headers=get_supabase_headers(),
                params={"order_id": f"eq.{order_id}", "select": "status"}
            )
            data = resp.json()
        if data and data[0].get("status") == "DONE":
            return {"paid": True}
        return {"paid": False}
    except Exception as e:
        print(f"[결제 확인 실패] {e}")
        return {"paid": False}


# ===== 데일리 카드 운세 생성 =====

class DailyCardRequest(BaseModel):
    pillars:      str            # 연/월/일/시주 (예: "甲子 乙丑 丙寅 丁卯")
    day_stem:     str            # 일간 (예: "丙")
    element:      str            # 일간 오행 (예: "火")
    element_kr:   str            # 오행 한글 (예: "화")
    element_dist: str            # 오행 분포 (예: "木2개 火3개...")
    gender:       str            # "M" / "F"
    today_str:    str            # 오늘 날짜 문자열
    today_pillar: str            # 오늘 일진
    year_pillar:  str            # 세운
    month_pillar: str            # 월건
    hour_note:    str            # 시주 참고 메모

@app.post("/api/daily-card")
@limiter.limit("10/minute")
async def daily_card(req: DailyCardRequest, request: Request):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return JSONResponse(status_code=500, content={"detail": "ANTHROPIC_API_KEY 누락"})

    prompt = f"""사주명리 전문가. 어려운 사주 한자 용어 절대 금지. 쉬운 일상 언어만 사용.

오늘: {req.today_str} / 일진: {req.today_pillar} / 세운: {req.year_pillar} / 월건: {req.month_pillar}

사용자 사주 (4기둥): {req.pillars}
일간: {req.day_stem}({req.element}) / 성별: {"남" if req.gender == "M" else "여"} / 오행분포: {req.element_dist}
시주 참고: {req.hour_note}

위 4기둥과 오늘 일진을 바탕으로 오늘 운세 분석. 등급 A+/A/B/C/D 중 하나. JSON만 응답(마크다운 없이):
{{"headline":"오늘을 표현하는 짧고 강한 한 마디","overall":"등급","overall_msg":"오늘 전체 운세 한 줄 따뜻하고 친근하게","categories":{{"investment":{{"grade":"등급","msg":"투자 구체적 조언 한 줄"}},"business":{{"grade":"등급","msg":"사업/직장 구체적 조언 한 줄"}},"study":{{"grade":"등급","msg":"학업/자기계발 구체적 조언 한 줄"}},"relations":{{"grade":"등급","msg":"인간관계 구체적 조언 한 줄"}}}},"lucky_num":숫자,"caution":"오늘 하지 말아야 할 것 한 줄"}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        return JSONResponse(content=result)
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=500, content={"detail": f"JSON 파싱 실패: {str(e)}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"detail": str(e)})


# ===== 데일리 카드 로그 =====

class DailyCardLogRequest(BaseModel):
    birth_year:      Optional[int] = None
    birth_month:     Optional[int] = None
    birth_day:       Optional[int] = None
    birth_hour:      Optional[int] = -1    # -1 = 시간 모름
    gender:          Optional[str] = ""
    day_pillar:      Optional[str] = ""
    element:         Optional[str] = ""
    overall_grade:   Optional[str] = ""
    invest_grade:    Optional[str] = ""
    business_grade:  Optional[str] = ""
    study_grade:     Optional[str] = ""
    relations_grade: Optional[str] = ""
    session_id:      Optional[str] = ""

@app.post("/api/daily-card-log")
@limiter.limit("10/minute")
async def log_daily_card(req: DailyCardLogRequest, request: Request):
    kst       = datetime.now(timezone(timedelta(hours=9)))
    client_ip = request.headers.get("x-forwarded-for", request.client.host)
    ip_hash   = hashlib.sha256(client_ip.encode()).hexdigest()[:16]
    await db_insert("daily_card_logs", {
        "birth_year":      req.birth_year,
        "birth_month":     req.birth_month,
        "birth_day":       req.birth_day,
        "birth_hour":      req.birth_hour,
        "gender":          req.gender,
        "day_pillar":      req.day_pillar,
        "element":         req.element,
        "overall_grade":   req.overall_grade,
        "invest_grade":    req.invest_grade,
        "business_grade":  req.business_grade,
        "study_grade":     req.study_grade,
        "relations_grade": req.relations_grade,
        "session_id":      req.session_id,
        "ip_hash":         ip_hash,
        "created_at":      kst.isoformat()
    })
    return {"ok": True}


# ===== 궁합 로그 =====

class GunghamLogRequest(BaseModel):
    p1_gender:  Optional[str] = ""
    p1_year:    Optional[int] = None
    p1_month:   Optional[int] = None
    p1_day:     Optional[int] = None
    p1_hour:    Optional[int] = -1
    p2_gender:  Optional[str] = ""
    p2_year:    Optional[int] = None
    p2_month:   Optional[int] = None
    p2_day:     Optional[int] = None
    p2_hour:    Optional[int] = -1
    session_id: Optional[str] = ""

@app.post("/api/gungham-log")
@limiter.limit("10/minute")
async def log_gungham(req: GunghamLogRequest, request: Request):
    kst       = datetime.now(timezone(timedelta(hours=9)))
    client_ip = request.headers.get("x-forwarded-for", request.client.host)
    ip_hash   = hashlib.sha256(client_ip.encode()).hexdigest()[:16]
    await db_insert("gungham_logs", {
        "p1_gender":  req.p1_gender,
        "p1_year":    req.p1_year,
        "p1_month":   req.p1_month,
        "p1_day":     req.p1_day,
        "p1_hour":    req.p1_hour,
        "p2_gender":  req.p2_gender,
        "p2_year":    req.p2_year,
        "p2_month":   req.p2_month,
        "p2_day":     req.p2_day,
        "p2_hour":    req.p2_hour,
        "session_id": req.session_id,
        "ip_hash":    ip_hash,
        "created_at": kst.isoformat()
    })
    return {"ok": True}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
