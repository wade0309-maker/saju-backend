from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import os
import uvicorn

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.post("/api/llm")
def get_model_list(req: dict):
    api_key = os.environ.get("GEMINI_API_KEY")
    # 구글 API에 모델 리스트를 요청하는 정식 주소입니다.
    url = f"https://generativelanguage.googleapis.com/v1/models?key={api_key}"
    
    try:
        response = requests.get(url)
        # 서버 응답 내용을 그대로 받아옵니다.
        return {"content": [{"text": str(response.json())}]}
    except Exception as e:
        return {"content": [{"text": f"Error: {str(e)}"}]}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
