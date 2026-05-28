from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
import uvicorn

app = FastAPI()

# 프론트엔드(Vercel)와의 통신을 위한 CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class GeminiRequest(BaseModel):
    baziData: str
    systemPrompt: str

@app.post("/api/llm")
def call_gemini_real(req: GeminiRequest):
    api_key = os.environ.get("GEMINI_API_KEY")
    
    # 1. 2.5 Pro 모델을 호출하는 공식 API 경로
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-pro:generateContent?key={api_key}"
    
    # 2. 리포트 생성을 위한 페이로드 구성
    payload = {
        "contents": [{"parts": [{"text": f"{req.systemPrompt}\n\n데이터: {req.baziData}"}]}],
        "generationConfig": {
            "temperature": 0.7,
            "topP": 0.95,
            "maxOutputTokens": 8192
        }
    }
    
    try:
        # 3. 구글 서버와 직접 통신
        response = requests.post(url, json=payload)
        data = response.json()
        
        # 4. 에러 발생 시 상세 확인
        if 'error' in data:
            return {"content": [{"text": f"구글 API 에러: {data['error']['message']}"}]}
            
        # 5. 정상 응답 추출
        text = data['candidates'][0]['content']['parts'][0]['text']
        return {"content": [{"text": text}]}
        
    except Exception as e:
        return {"content": [{"text": f"서버 연산 에러: {str(e)}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
