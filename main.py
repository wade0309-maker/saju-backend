from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic
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

class AnalysisRequest(BaseModel):
    baziData: str
    systemPrompt: str

@app.post("/api/llm")
def call_claude(req: AnalysisRequest):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"content": [{"text": "서버 환경 변수 에러: ANTHROPIC_API_KEY가 누락되었습니다."}]}
    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2500,
            messages=[
                {
                    "role": "user",
                    "content": f"{req.systemPrompt}\n\n[내담자 명식 데이터]\n{req.baziData}"
                }
            ]
        )
        text = message.content[0].text
        return {"content": [{"text": text}]}
    except Exception as e:
        return {"content": [{"text": f"서버 내부 연산 실패: {str(e)}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
