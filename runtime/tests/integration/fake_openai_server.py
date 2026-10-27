from fastapi import FastAPI


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [
            {
                "id": "verilex-smoke-model",
                "object": "model",
                "created": 0,
                "owned_by": "verilex",
            }
        ],
    }


@app.post("/v1/chat/completions")
def completion():
    return {
        "id": "verilex-smoke-completion",
        "object": "chat.completion",
        "created": 0,
        "model": "verilex-smoke-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop",
            }
        ],
    }
