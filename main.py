from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
import os

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
    # 1. 렌더(Render) 환경 변수에서 Gemini API 키를 안전하게 불러옵니다.
    api_key = os.environ.get("GEMINI_API_KEY")
    
    if not api_key:
        return {"content": [{"text": "시스템 에러: 백엔드 서버에 Gemini API 키가 설정되지 않았습니다."}]}

    try:
        # 2. Gemini API 세팅
        genai.configure(api_key=api_key)
        
        # 3. 모델 설정 (system_instruction을 지원하는 1.5 Pro 모델 적용)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=req.systemPrompt
        )
        
        # 4. 프롬프트 전달 및 응답 생성
        response = model.generate_content(
            f"다음 명식 데이터로 완벽한 프리미엄 리포트를 작성해.\n\n{req.baziData}"
        )
        
        # 5. 기존 프론트엔드 코드(Claude 규격)가 깨지지 않도록 동일한 JSON 구조로 변환하여 반환
        return {"content": [{"text": response.text}]}
        
    except Exception as e:
        # 통신 실패 시 프론트엔드가 다운되지 않도록 에러 메시지 반환
        return {"content": [{"text": f"Gemini AI 연산 중 문제가 발생했습니다: {str(e)}"}]}