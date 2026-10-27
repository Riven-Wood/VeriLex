import json
from tests.api.test_threads_scene import api_fixture, HEADERS


def test_search_body_escaped_unicode_and_excludes_metadata(api_fixture):
    client, threads = api_fixture
    a = threads.create("一般会话", "model-1", provider_id="provider-1")
    b = threads.create("合同标题", "model-1", provider_id="provider-1")
    db = threads.repository._database
    with db.connect() as c:
        for seq, text in enumerate(["合同中\\n引号\"与 Straße", "百分比% 和_"], 1):
            c.execute("INSERT INTO messages (id,thread_id,turn_id,sequence,role,content_json,created_at) VALUES (?,?,NULL,?,'user',?,?)",
                      (f"m{seq}", a.id, seq, json.dumps({"version": 1, "text": text, "attachments": []}), "2026-09-15"))
    response = client.get("/v1/threads/search", params={"q": "合同"}, headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert [r["thread"]["id"] for r in response.json()] == [b.id, a.id]
    for query in ['引号"', 'STRASSE', '%', '_']:
        rows = client.get("/v1/threads/search", params={"q": query}, headers=HEADERS).json()
        assert [r["thread"]["id"] for r in rows] == [a.id]
    assert client.get("/v1/threads/search", params={"q": "attachments"}, headers=HEADERS).json() == []
    threads.archive(a.id)
    assert client.get("/v1/threads/search", params={"q": "Straße"}, headers=HEADERS).json()
    threads.trash(a.id)
    assert client.get("/v1/threads/search", params={"q": "Straße"}, headers=HEADERS).json() == []
    assert client.get("/v1/threads/search", params={"q": "   "}, headers=HEADERS).status_code == 422
    assert client.get("/v1/threads/search", params={"q": "a" * 201}, headers=HEADERS).status_code == 422
    assert client.get("/v1/threads/search", params={"q": "合同"}).status_code in (401, 403)
