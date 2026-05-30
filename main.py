from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
        return JSONResponse(
            status_code=500,
            content={"detail": "ANTHROPIC_API_KEY 누락"}
        )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2500,
            messages=[{
                "role": "user",
                "content": f"{req.systemPrompt}\n\n[내담자 명식 데이터]\n{req.baziData}"
            }]
        )
        text = message.content[0].text
        return {"content": [{"text": text}]}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": str(e)}
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
