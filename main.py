from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import asyncio

app = FastAPI()

# Vercel(프론트엔드)에서 오는 요청을 허용하기 위한 CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # 상용화 시에는 Vercel 도메인만 넣습니다.
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "AI 사주 백엔드 서버가 정상 작동 중입니다."}

# 이 부분이 504 에러를 해결할 핵심 테스트 API입니다.
@app.post("/api/premium")
async def generate_premium_saju():
    # 실제로는 여기서 AI 프롬프트가 실행됩니다. 
    # Vercel의 25초 타임아웃을 넘기는지 테스트하기 위해 일부러 40초를 대기시킵니다.
    await asyncio.sleep(40) 
    
    return {
        "status": "success",
        "report": "40초가 걸린 아주 디테일한 프리미엄 사주 분석 결과입니다. 타임아웃을 무사히 통과했습니다!"
    }