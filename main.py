from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
import os
import uvicorn

app = FastAPI()

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
    if not api_key:
        return {"content": [{"text": "API Key Error"}]}

    try:
        genai.configure(api_key=api_key)
        # 구글이 API 버전과 상관없이 자동으로 매핑해주는 가장 안전한 공식 모델명
        model = genai.GenerativeModel("gemini-1.5-flash") 
        
        response = model.generate_content(
            f"{req.systemPrompt}\n\n데이터: {req.baziData}"
        )
        return {"content": [{"text": response.text}]}
    except Exception as e:
        return {"content": [{"text": f"Error: {str(e)}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
