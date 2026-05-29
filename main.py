from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
import uvicorn

app = FastAPI()

# 루트 경로 접속 확인 (Health Check용)
@app.get("/")
def read_root():
    return {"status": "success", "message": "Paljaguild Premium Pro Server is running"}

# 프론트엔드(Vercel)와의 안전한 자원 공유를 위한 CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GeminiRequest(BaseModel):
    baziData: str
    systemPrompt: str

@app.post("/api/llm")
def call_gemini_real(req: GeminiRequest):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"content": [{"text": "서버 환경 변수 에러: GEMINI_API_KEY가 누락되었습니다."}]}
    
    # 계정 연동 상태가 확인된 최상위 2.5 Pro 모델의 정식 v1 API 경로
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={api_key}"
    
    # 일관성 확보 및 토큰 최대 확보를 위한 페이로드 설계
    payload = {
        "contents": [{"parts": [{"text": f"{req.systemPrompt}\n\n[내담자 명식 데이터]\n{req.baziData}"}]}],
        "generationConfig": {
            "temperature": 0.3,  # 결과값의 무분별한 변동을 제어하기 위한 최적 밸런스
            "topP": 0.95,
            "maxOutputTokens": 3000  # 13단계 보고서 완결을 위한 최대 대역폭 확보
        }
    }
    
    try:
        response = requests.post(url, json=payload)
        data = response.json()
        
        # 구글 API 내부 에러 트래킹
        if 'error' in data:
            return {"content": [{"text": f"구글 API 반환 에러: {data['error']['message']}"}]}
            
        text = data['candidates'][0]['content']['parts'][0]['text']
        return {"content": [{"text": text}]}
    except Exception as e:
        return {"content": [{"text": f"서버 내부 연산 실패: {str(e)}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
