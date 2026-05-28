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
    # 구글 정식 API 엔드포인트 (v1 버전 사용)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": f"{req.systemPrompt}\n\n데이터: {req.baziData}"}]}]
    }
    
    try:
        response = requests.post(url, json=payload)
        data = response.json()
        text = data['candidates'][0]['content']['parts'][0]['text']
        return {"content": [{"text": text}]}
    except Exception as e:
        return {"content": [{"text": f"Error: {str(e)} - 상세 데이터: {str(data if 'data' in locals() else '')}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
