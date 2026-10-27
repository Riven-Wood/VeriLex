from fastapi import FastAPI


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/tags")
def tags():
    return {"models": [{"name": "verilex-fake-ollama-model"}]}
