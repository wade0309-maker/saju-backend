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
    systemPrompt: str
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

    tab_name  = infer_tab_name(req.systemPrompt)
    max_tokens = TAB_MAX_TOKENS.get(tab_name, 3500)

    # 백그라운드 로깅용 정보 미리 수집
    client_ip = request.headers.get("x-forwarded-for", request.client.host)
    ip_hash   = hashlib.sha256(client_ip.encode()).hexdigest()[:16]
    parsed    = parse_bazi_data(req.baziData)

    async def generate():
        full_text = ""
        try:
            client = anthropic.Anthropic(api_key=api_key)
            with client.messages.stream(
                model="claude-haiku-4-5-20251001",
                max_tokens=max_tokens,
                messages=[{
                    "role": "user",
                    "content": f"{req.systemPrompt}\n\n[내담자 명식 데이터]\n{req.baziData}"
                }]
            ) as stream:
                for text_chunk in stream.text_stream:
                    full_text += text_chunk
                    # SSE 형식으로 청크 전송
                    yield f"data: {json.dumps({'text': text_chunk}, ensure_ascii=False)}\n\n"

            # 스트리밍 완료 신호
            yield "data: [DONE]\n\n"

            # 완료 후 백그라운드 로깅 (스트리밍 끝난 뒤 실행)
            try:
                log_to_sheets(req.baziData, tab_name, req.isPaid)
            except Exception:
                pass

            try:
                kst = datetime.now(timezone(timedelta(hours=9)))
                import asyncio
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
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

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
