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
    # v1beta를 완전히 버리고, 구글이 보장하는 v1 정식 엔드포인트 사용
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": f"{req.systemPrompt}\n\n데이터: {req.baziData}"}]}]
    }
    
    try:
        response = requests.post(url, json=payload)
        data = response.json()
        
        # 404가 발생하면 응답 데이터를 그대로 로그에 찍어 원인을 파악합니다
        if response.status_code != 200:
            return {"content": [{"text": f"Error {response.status_code}: {data}"}]}
            
        text = data['candidates'][0]['content']['parts'][0]['text']
        return {"content": [{"text": text}]}
    except Exception as e:
        return {"content": [{"text": f"Exception: {str(e)}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
