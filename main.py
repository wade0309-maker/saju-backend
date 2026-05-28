from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
import os
import uvicorn

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "success", "message": "팔자길드 Pro 엔진 가동 완료"}

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
        # 안정적인 정식 명칭 사용
        model = genai.GenerativeModel(
            model_name="gemini-1.5-pro",
            system_instruction=req.systemPrompt
        )
        response = model.generate_content(
            f"다음 명식 데이터로 리포트를 작성해.\n\n{req.baziData}"
        )
        return {"content": [{"text": response.text}]}
    except Exception as e:
        return {"content": [{"text": f"Error: {str(e)}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
