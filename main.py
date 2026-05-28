from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
import uvicorn

app = FastAPI()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class GeminiRequest(BaseModel):
    baziData: str
    systemPrompt: str

@app.post("/api/llm")
def call_gemini_real(req: GeminiRequest):
    api_key = os.environ.get("GEMINI_API_KEY")
    # [핵심 수정] v1beta를 v1으로 변경 (정식 릴리즈 경로)
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": f"{req.systemPrompt}\n\n데이터: {req.baziData}"}]}]
    }
    
    try:
        response = requests.post(url, json=payload)
        data = response.json()
        
        # 상세 에러 발생 시 로그 확인용
        if 'error' in data:
            return {"content": [{"text": f"구글 API 에러: {data['error']['message']}"}]}
            
        text = data['candidates'][0]['content']['parts'][0]['text']
        return {"content": [{"text": text}]}
    except Exception as e:
        return {"content": [{"text": f"연산 에러: {str(e)}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
