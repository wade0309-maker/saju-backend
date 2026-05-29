from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
import uvicorn

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "success", "message": "Paljaguild Premium Pro Server is running"}

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
def call_gemini(req: GeminiRequest):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"content": [{"text": "서버 환경 변수 에러: GEMINI_API_KEY가 누락되었습니다."}]}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": f"{req.systemPrompt}\n\n[내담자 명식 데이터]\n{req.baziData}"}]}],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.95,
            "maxOutputTokens": 2500
        }
    }

    try:
        response = requests.post(url, json=payload, timeout=55)
        data = response.json()
        if 'error' in data:
            return {"content": [{"text": f"구글 API 에러: {data['error']['message']}"}]}
        text = data['candidates'][0]['content']['parts'][0]['text']
        return {"content": [{"text": text}]}
    except Exception as e:
        return {"content": [{"text": f"서버 내부 연산 실패: {str(e)}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
