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
    # API 키 확인
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"content": [{"text": "API Key Error"}]}

    try:
        # 라이브러리 설정 단순화
        genai.configure(api_key=api_key)
        
        # [핵심 수정] 명시적 버전 경로 없이 모델만 호출
        model = genai.GenerativeModel("gemini-1.5-flash")
        
        # system_instruction은 호출 시점에 포함하거나 
        # generate_content 호출 시 프롬프트로 통합
        full_prompt = f"{req.systemPrompt}\n\n데이터: {req.baziData}"
        
        response = model.generate_content(full_prompt)
        
        return {"content": [{"text": response.text}]}
    except Exception as e:
        # 에러 발생 시 상세 원인 반환
        return {"content": [{"text": f"Error: {str(e)}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
