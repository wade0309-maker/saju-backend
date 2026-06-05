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
    "deepinterest": 3500,  # 관심사 분석
    "trait":        3000,  # 유아 기질 (섹션 적음)
    "parent":       3000,  # 부모 전략 (섹션 적음)
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
