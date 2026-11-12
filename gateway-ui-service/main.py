import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gateway-ui-service")

USER_SVC_URL = os.getenv("USER_SVC_URL", "http://user-service:8001").rstrip("/")
ADVISORY_SVC_URL = os.getenv("ADVISORY_SVC_URL", "http://advisory-service:8002").rstrip("/")
SEARCH_SVC_URL = os.getenv("SEARCH_SVC_URL", "http://search-rec-service:8003").rstrip("/")
CHATBOT_SVC_URL = os.getenv("CHATBOT_SVC_URL", "http://chatbot-service:8004").rstrip("/")

BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"

app = FastAPI(
    title="MedPulse AI - API Gateway & Frontend UI",
    description="Unified FastAPI Gateway and Clinical Dashboard for MedPulse AI.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def proxy_request(
    method: str,
    url: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Any] = None,
    timeout: int = 15,
):
    try:
        resp = requests.request(
            method=method,
            url=url,
            params=params,
            json=json_body,
            timeout=timeout,
        )
        try:
            content = resp.json()
        except ValueError:
            content = {"detail": resp.text}

        if resp.status_code >= 400:
            return JSONResponse(status_code=resp.status_code, content=content)
        return JSONResponse(status_code=resp.status_code, content=content)
    except requests.Timeout:
        logger.error(f"Upstream timeout calling {method} {url}")
        raise HTTPException(status_code=504, detail=f"Upstream service timeout: {url}")
    except requests.RequestException as exc:
        logger.error(f"Upstream connection error calling {method} {url}: {exc}")
        raise HTTPException(status_code=502, detail=f"Upstream service unavailable ({url}): {exc}")


@app.get("/", include_in_schema=False)
def serve_ui():
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(INDEX_FILE, media_type="text/html")


@app.get("/health")
def gateway_health():
    return {"status": "healthy", "service": "gateway-ui-service"}


@app.get("/api/health")
def aggregated_health():
    services = {
        "user-service": f"{USER_SVC_URL}/health",
        "advisory-service": f"{ADVISORY_SVC_URL}/health",
        "search-rec-service": f"{SEARCH_SVC_URL}/health",
        "chatbot-service": f"{CHATBOT_SVC_URL}/health",
    }
    report = {"gateway": "healthy", "services": {}}
    for name, url in services.items():
        try:
            resp = requests.get(url, timeout=3)
            if resp.status_code == 200:
                report["services"][name] = resp.json()
            else:
                report["services"][name] = {"status": "unhealthy", "code": resp.status_code}
        except Exception as exc:
            report["services"][name] = {"status": "unreachable", "error": str(exc)}
    return report


@app.get("/api/users")
def get_users(role: Optional[str] = Query(default=None)):
    params = {"role": role} if role else None
    return proxy_request("GET", f"{USER_SVC_URL}/users", params=params)


@app.post("/api/users")
async def create_user(request: Request):
    body = await request.json()
    return proxy_request("POST", f"{USER_SVC_URL}/users", json_body=body)


@app.get("/api/appointments")
def get_appointments():
    return proxy_request("GET", f"{USER_SVC_URL}/appointments")


@app.post("/api/appointments")
async def create_appointment(request: Request):
    body = await request.json()
    return proxy_request("POST", f"{USER_SVC_URL}/appointments", json_body=body)


@app.get("/api/advisories")
def get_advisories(
    audience: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
):
    params = {}
    if audience:
        params["audience"] = audience
    if category:
        params["category"] = category
    return proxy_request("GET", f"{ADVISORY_SVC_URL}/advisories", params=params or None)


@app.post("/api/advisories")
async def create_advisory(request: Request):
    body = await request.json()
    return proxy_request("POST", f"{ADVISORY_SVC_URL}/advisories", json_body=body)


@app.get("/api/search")
def search_advisories(
    q: str = Query(..., min_length=1),
    role: str = Query(default="All"),
    top_k: int = Query(default=3, ge=1, le=20),
):
    return proxy_request(
        "GET",
        f"{SEARCH_SVC_URL}/search",
        params={"q": q, "role": role, "top_k": top_k},
    )


@app.post("/api/reindex")
def reindex_advisories():
    return proxy_request("POST", f"{SEARCH_SVC_URL}/reindex", timeout=30)


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    return proxy_request("POST", f"{CHATBOT_SVC_URL}/chat", json_body=body, timeout=30)


@app.get("/api/chat/history")
def chat_history(
    session_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    params: Dict[str, Any] = {"limit": limit}
    if session_id:
        params["session_id"] = session_id
    return proxy_request("GET", f"{CHATBOT_SVC_URL}/chat/history", params=params)
