from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Vercel에서 넘겨주는 데이터를 받을 그릇(구조)
class ClaudeRequest(BaseModel):
    baziData: str
    systemPrompt: str

# 프론트엔드가 두드릴 새로운 문 (/api/llm)
@app.post("/api/llm")
async def call_claude_mock(req: ClaudeRequest):
    # 실제 API가 없으므로, 3초간 AI가 분석하는 척만 합니다.
    await asyncio.sleep(3)
    
    # 가짜 AI 리포트 결과 생성
    fake_report = f"""
    [👑 프리미엄 리포트 모의 통신 성공]
    
    프론트엔드에서 렌더(Render) 백엔드로 아래의 데이터가 무사히 도착했습니다:
    --------------------------------------------------
    {req.baziData}
    --------------------------------------------------
    
    * 이 텍스트는 아직 실제 Claude API가 연결되지 않아 출력되는 테스트용 결과물입니다.
    * Vercel의 25초 타임아웃 룰을 완전히 벗어나, 독립 백엔드 통신 파이프라인 구축에 완벽하게 성공하셨습니다!
    """
    
    # 프론트엔드가 기대하는 응답 형태로 반환
    return {
        "content": [{"text": fake_report}]
    }