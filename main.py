from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
import os

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "success"}

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
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=req.systemPrompt
        )
        response = model.generate_content(
            f"다음 명식 데이터로 리포트를 작성해.\n\n{req.baziData}"
        )
        return {"content": [{"text": response.text}]}
    except Exception as e:
        return {"content": [{"text": f"Error: {str(e)}"}]}
