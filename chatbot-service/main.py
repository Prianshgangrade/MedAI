import os
import json
import uuid
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.errors import PyMongoError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chatbot-service")

LOCAL_MODE = os.getenv("LOCAL_MODE", "false").lower() == "true"
LOCAL_CHAT_PATH = os.getenv("LOCAL_CHAT_PATH", "chat_history_local.json")

SEARCH_SVC_URL = os.getenv("SEARCH_SVC_URL", "http://search-rec-service:8003/search")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongodb:27017/")
MONGO_DB = os.getenv("MONGO_DB", "medpulse_db")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

app = FastAPI(
    title="MedPulse AI - Symptom & Advisory Chatbot Service",
    description="RAG-powered clinical triage and advisory chatbot backed by FAISS search, Gemini API (optional), and MongoDB.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=2, max_length=2000)
    role: str = Field(default="Patient", pattern="^(All|Doctor|Patient)$")
    session_id: Optional[str] = Field(default=None, max_length=100)


class ChatResponse(BaseModel):
    session_id: str
    role: str
    user_message: str
    bot_response: str
    mode: str
    matched_advisories: List[Dict]
    recommendations: List[Dict]
    timestamp: str


mongo_client: Optional[MongoClient] = None
local_chat_lock = threading.Lock()


def load_local_chat_history() -> List[Dict]:
    if not os.path.exists(LOCAL_CHAT_PATH):
        return []
    with open(LOCAL_CHAT_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def append_local_chat_turn(record: Dict) -> None:
    with local_chat_lock:
        docs = load_local_chat_history()
        docs.append(record)
        with open(LOCAL_CHAT_PATH, "w", encoding="utf-8") as fh:
            json.dump(docs, fh, ensure_ascii=False, indent=2)


def get_chat_collection():
    global mongo_client
    if mongo_client is None:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo_client[MONGO_DB]
    return db["chat_history"]


def fetch_grounded_advisories(query: str, role: str) -> Dict:
    try:
        resp = requests.get(
            SEARCH_SVC_URL,
            params={"q": query, "role": role, "top_k": 3},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error(f"Error calling search-rec-service at {SEARCH_SVC_URL}: {exc}")
        raise HTTPException(
            status_code=502,
            detail=f"Unable to retrieve advisory context from search-rec-service: {exc}",
        )


def build_deterministic_triage_response(
    user_message: str,
    role: str,
    results: List[Dict],
    recommendations: List[Dict],
) -> str:
    if not results:
        return (
            "### MedPulse AI Clinical Triage Summary\n"
            f"**Query:** {user_message}\n\n"
            "No specific WHO/MoHFW/ICMR advisory matched your query for the selected role. "
            "If you are experiencing severe symptoms (chest pain, difficulty breathing, persistent high fever, or confusion), "
            "seek immediate medical care or dial **108 / 112**."
        )

    primary = results[0]
    primary_symptoms = ", ".join(primary.get("symptoms", [])[:5])
    lower_msg = user_message.lower()
    red_flags = [
        flag
        for flag in [
            "difficulty breathing",
            "shortness of breath",
            "chest pain",
            "bleeding",
            "confusion",
            "fainting",
            "hemoptysis",
            "unconscious",
        ]
        if flag in lower_msg
    ]

    urgency_banner = (
        "**URGENT TRIAGE ALERT:** Red-flag symptoms detected. Seek immediate emergency evaluation (108/112 or nearest ER)."
        if red_flags
        else "**Triage Level:** Routine to Priority Clinical Evaluation recommended based on symptom progression."
    )

    causes_lines = []
    for idx, adv in enumerate(results, start=1):
        score_pct = round(float(adv.get("similarity_score", 0.0)) * 100, 1)
        causes_lines.append(
            f"{idx}. **{adv.get('title')}** (`{adv.get('advisory_id')}` | {adv.get('source')} | Match: {score_pct}%)"
        )

    if role == "Doctor":
        next_steps = (
            "- Order confirmatory diagnostic tests specified in the primary advisory (e.g., NS1/IgM ELISA, CBNAAT/TrueNat, RT-PCR, or culture before broad-spectrum antibiotics).\n"
            "- Stratify severity for warning signs, follow MoHFW/ICMR first-line dosing protocols, and report notifiable diseases via IDSP / Nikshay portals.\n"
            "- Review vector-recommended companion guidelines for co-morbidity or stewardship alignment."
        )
    else:
        next_steps = (
            "- Maintain hydration (ORS/clean fluids) and avoid self-medicating with NSAIDs (like ibuprofen/aspirin) or unprescribed antibiotics.\n"
            "- Book an appointment with a specialist via the **Doctor Roster & Appointment** panel if symptoms persist beyond 48 hours.\n"
            "- Seek urgent care immediately if warning signs (breathing difficulty, persistent vomiting, bleeding, or confusion) develop."
        )

    rec_lines = ""
    if recommendations:
        rec_items = [
            f"- **{r.get('title')}** (`{r.get('advisory_id')}` - {r.get('source')})"
            for r in recommendations
        ]
        rec_lines = "\n\n#### Related Guidelines to Review\n" + "\n".join(rec_items)

    return (
        f"### Clinical Triage & Official Guidance ({role} View)\n"
        f"{urgency_banner}\n\n"
        f"#### 1. Possible Clinical Considerations\n"
        + "\n".join(causes_lines)
        + f"\n\n#### 2. Primary Official WHO / MoHFW / ICMR Protocol\n"
        f"**Key Associated Symptoms:** {primary_symptoms}\n\n"
        f"{primary.get('content', '')}\n\n"
        f"#### 3. Recommended Next Steps\n"
        f"{next_steps}"
        f"{rec_lines}"
    )


def generate_gemini_rag_response(
    user_message: str,
    role: str,
    results: List[Dict],
    recommendations: List[Dict],
) -> Optional[str]:
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai

        context_blocks = []
        for adv in results:
            context_blocks.append(
                f"[{adv.get('advisory_id')}] {adv.get('title')} ({adv.get('source')}, Audience: {adv.get('audience')}, "
                f"Similarity: {adv.get('similarity_score')}):\n"
                f"Symptoms: {', '.join(adv.get('symptoms', []))}\n"
                f"Protocol: {adv.get('content')}"
            )
        context_text = "\n\n".join(context_blocks) if context_blocks else "No matching advisories."

        rec_text = "\n".join(
            f"- [{r.get('advisory_id')}] {r.get('title')} ({r.get('source')})"
            for r in recommendations
        )

        prompt = (
            "You are MedPulse AI, a grounded clinical advisory assistant following WHO, MoHFW, and ICMR guidelines.\n"
            f"Target Audience Role: {role}\n"
            "Use ONLY the official advisory context below to answer. Cite advisory IDs and sources accurately. "
            "Structure your response into: (1) Possible Causes & Triage Urgency, (2) Official WHO/MoHFW/ICMR Protocol, "
            "and (3) Recommended Next Steps. Include a brief medical disclaimer.\n\n"
            f"Retrieved Advisories:\n{context_text}\n\n"
            f"Related Vector Recommendations:\n{rec_text}\n\n"
            f"User Query / Symptoms: {user_message}"
        )

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = getattr(response, "text", None)
        if text and text.strip():
            return text.strip()
    except Exception as exc:
        logger.warning(f"Gemini API call failed ({exc}); falling back to deterministic clinical triage template.")
    return None


@app.get("/health")
def health_check():
    if LOCAL_MODE:
        with local_chat_lock:
            history_count = len(load_local_chat_history())
        return {
            "status": "healthy",
            "service": "chatbot-service",
            "database": "json-local",
            "gemini_configured": bool(GEMINI_API_KEY),
            "stored_turns": history_count,
        }

    mongo_status = "disconnected"
    history_count = 0
    try:
        col = get_chat_collection()
        col.database.client.admin.command("ping")
        mongo_status = "connected"
        history_count = col.count_documents({})
    except Exception as exc:
        logger.warning(f"MongoDB health check warning: {exc}")

    return {
        "status": "healthy" if mongo_status == "connected" else "degraded",
        "service": "chatbot-service",
        "database": mongo_status,
        "gemini_configured": bool(GEMINI_API_KEY),
        "stored_turns": history_count,
    }


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    session_id = (payload.session_id or "").strip() or str(uuid.uuid4())
    search_data = fetch_grounded_advisories(payload.message, payload.role)
    results = search_data.get("results", [])
    recommendations = search_data.get("recommendations", [])

    gemini_answer = generate_gemini_rag_response(payload.message, payload.role, results, recommendations)
    if gemini_answer:
        bot_response = gemini_answer
        mode = "gemini-rag"
    else:
        bot_response = build_deterministic_triage_response(
            payload.message, payload.role, results, recommendations
        )
        mode = "deterministic-triage"

    timestamp = datetime.now(timezone.utc).isoformat()
    record = {
        "session_id": session_id,
        "role": payload.role,
        "user_message": payload.message,
        "bot_response": bot_response,
        "mode": mode,
        "matched_advisories": results,
        "recommendations": recommendations,
        "timestamp": timestamp,
    }

    if LOCAL_MODE:
        append_local_chat_turn(dict(record))
        return ChatResponse(**record)

    try:
        col = get_chat_collection()
        col.insert_one(dict(record))
    except PyMongoError as exc:
        logger.error(f"Failed to persist chat turn to MongoDB: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to save chat history: {exc}")

    return ChatResponse(**record)


@app.get("/chat/history")
def get_chat_history(
    session_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    if LOCAL_MODE:
        with local_chat_lock:
            docs = load_local_chat_history()
        if session_id:
            docs = [d for d in docs if d.get("session_id") == session_id]
        docs = docs[-limit:]
        return {"history": docs, "count": len(docs)}

    try:
        col = get_chat_collection()
        query = {}
        if session_id:
            query["session_id"] = session_id
        docs = list(col.find(query, {"_id": 0}).sort("timestamp", -1).limit(limit))
        docs.reverse()
        return {"history": docs, "count": len(docs)}
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"MongoDB query error: {exc}")
