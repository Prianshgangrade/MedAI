import os
import json
import time
import logging
import threading
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.errors import PyMongoError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("advisory-service")

LOCAL_MODE = os.getenv("LOCAL_MODE", "false").lower() == "true"
LOCAL_ADVISORIES_PATH = os.getenv("LOCAL_ADVISORIES_PATH", "advisories_local.json")

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongodb:27017/")
MONGO_DB = os.getenv("MONGO_DB", "medpulse_db")

app = FastAPI(
    title="MedPulse AI - Advisory Service",
    description="Manages WHO and Indian Government (MoHFW/ICMR) health advisories in MongoDB.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AdvisoryCreate(BaseModel):
    advisory_id: str = Field(..., min_length=3, max_length=50)
    source: str = Field(..., min_length=2, max_length=120)
    audience: str = Field(..., pattern="^(Doctor|Patient|Both)$")
    title: str = Field(..., min_length=5, max_length=250)
    category: str = Field(..., min_length=2, max_length=100)
    symptoms: List[str] = Field(default_factory=list)
    content: str = Field(..., min_length=10)


class AdvisoryResponse(AdvisoryCreate):
    pass


SEED_ADVISORIES = [
    {
        "advisory_id": "ADV-WHO-MOHFW-001",
        "source": "MoHFW (NCVBDC) & WHO",
        "audience": "Both",
        "title": "Dengue and Chikungunya Clinical Management & Vector Control Advisory",
        "category": "Vector-Borne Diseases",
        "symptoms": [
            "high fever",
            "severe headache",
            "retro-orbital pain",
            "joint pain",
            "myalgia",
            "maculopapular rash",
            "thrombocytopenia",
        ],
        "content": (
            "Per MoHFW National Center for Vector Borne Diseases Control (NCVBDC) and WHO Dengue guidelines: "
            "Suspect Dengue or Chikungunya in patients presenting with acute febrile illness (2-7 days), "
            "retro-orbital pain, severe arthralgia, or rash. Order NS1 antigen test within days 1-5 and IgM ELISA after day 5. "
            "Avoid NSAIDs (ibuprofen, aspirin, diclofenac) due to bleeding risk; use paracetamol for fever and pain. "
            "Ensure aggressive oral hydration with ORS; monitor platelet count, hematocrit, and warning signs "
            "(persistent vomiting, abdominal pain, mucosal bleeding, lethargy). Eliminate stagnant water around homes."
        ),
    },
    {
        "advisory_id": "ADV-WHO-NCDC-002",
        "source": "WHO & MoHFW (NCDC)",
        "audience": "Doctor",
        "title": "H5N1 Avian Influenza Surveillance, Infection Control & Oseltamivir Protocol",
        "category": "Zoonotic & Respiratory Infections",
        "symptoms": [
            "high fever",
            "cough",
            "shortness of breath",
            "conjunctivitis",
            "pneumonia",
            "acute respiratory distress",
            "poultry exposure",
        ],
        "content": (
            "Per WHO Global Influenza Programme and MoHFW National Centre for Disease Control (NCDC) guidelines: "
            "Clinicians evaluating Severe Acute Respiratory Infection (SARI) with recent poultry, wild bird, or dairy cattle exposure "
            "must immediately isolate the patient under airborne and droplet precautions (N95 respirator, eye protection, gown). "
            "Collect nasopharyngeal and oropharyngeal swabs in VTM for RT-PCR testing at designated VRDL/ICMR labs. "
            "Initiate empirical Oseltamivir (75 mg twice daily for 5 days in adults) within 48 hours of symptom onset without waiting for lab confirmation. "
            "Notify District Surveillance Officer (IDSP) immediately."
        ),
    },
    {
        "advisory_id": "ADV-ICMR-WHO-003",
        "source": "ICMR (AMSP) & WHO AWaRe",
        "audience": "Doctor",
        "title": "Antimicrobial Stewardship Guidelines & WHO AWaRe Classification",
        "category": "Antimicrobial Resistance",
        "symptoms": [
            "bacterial infection",
            "sepsis",
            "urinary tract infection",
            "community acquired pneumonia",
            "persistent fever",
            "surgical site infection",
        ],
        "content": (
            "Per ICMR Antimicrobial Stewardship Program (AMSP) and WHO AWaRe Antibiotic Book: "
            "Avoid empirical broad-spectrum antibiotics for viral upper respiratory tract infections and uncomplicated acute watery diarrhea. "
            "Obtain blood, urine, or sputum cultures prior to initiating empirical antimicrobials in hospitalized patients. "
            "Prefer WHO 'Access' group antibiotics (e.g., Amoxicillin, Nitrofurantoin, Doxycycline) as first-line therapy; "
            "restrict 'Watch' (Carbapenems, Piperacillin-Tazobactam, Fluoroquinolones) and 'Reserve' agents (Colistin, Linezolid) "
            "with mandatory 48-72 hour de-escalation review based on culture sensitivity."
        ),
    },
    {
        "advisory_id": "ADV-MOHFW-NDMA-004",
        "source": "MoHFW & NDMA (India)",
        "audience": "Patient",
        "title": "Extreme Heatwave Prevention, Heat Exhaustion & Heatstroke First-Aid Advisory",
        "category": "Environmental & Climate Health",
        "symptoms": [
            "dizziness",
            "heavy sweating",
            "nausea",
            "muscle cramps",
            "confusion",
            "hot dry skin",
            "hyperthermia",
            "dehydration",
        ],
        "content": (
            "Per MoHFW National Programme on Climate Change & Human Health (NPCCHH) and NDMA Heat Action Plan: "
            "Avoid outdoor sun exposure between 12:00 PM and 3:00 PM. Drink water, Oral Rehydration Solution (ORS), buttermilk (chaas), "
            "or lemon water frequently even if not thirsty. Wear lightweight, light-colored loose cotton clothing and cover your head. "
            "Warning signs of heatstroke include body temperature above 40C (104F), hot dry skin, confusion, or fainting. "
            "Move affected individuals to shade immediately, apply wet towels or cold water sponging to neck/axilla/groin, and call 108/112 emergency services."
        ),
    },
    {
        "advisory_id": "ADV-ICMR-WHO-005",
        "source": "ICMR-NCDIR & WHO HEARTS (IHCI)",
        "audience": "Both",
        "title": "Hypertension Screening, Lifestyle Modification & IHCI Treatment Protocol",
        "category": "Non-Communicable Diseases",
        "symptoms": [
            "elevated blood pressure",
            "occipital headache",
            "blurred vision",
            "chest discomfort",
            "palpitations",
            "asymptomatic hypertension",
        ],
        "content": (
            "Per India Hypertension Control Initiative (IHCI), ICMR-NCDIR, and WHO HEARTS protocol: "
            "Screen all adults aged 18+ opportunistically; diagnose hypertension when SBP >= 140 mmHg or DBP >= 90 mmHg on two separate readings. "
            "For Patients: restrict dietary sodium below 5g/day, adopt DASH-style fruits/vegetables, perform 150 minutes/week of brisk walking, "
            "avoid tobacco/alcohol, and never skip prescribed blood pressure medication. "
            "For Doctors: initiate Amlodipine 5 mg once daily as first-line monotherapy; escalate to Amlodipine + Telmisartan 40 mg and Chlorthalidone/HCTZ "
            "per state IHCI protocol if target BP (<140/90 mmHg) is not achieved within 30 days."
        ),
    },
    {
        "advisory_id": "ADV-NTEP-WHO-006",
        "source": "MoHFW (NTEP) & WHO",
        "audience": "Both",
        "title": "National Tuberculosis Elimination Programme (NTEP) Screening & Nikshay Guidelines",
        "category": "Infectious Diseases",
        "symptoms": [
            "persistent cough over 2 weeks",
            "evening fever",
            "night sweats",
            "unexplained weight loss",
            "hemoptysis",
            "loss of appetite",
        ],
        "content": (
            "Per MoHFW National Tuberculosis Elimination Programme (NTEP) and WHO Consolidated TB Guidelines: "
            "Any person with a cough lasting >= 2 weeks, fever >= 2 weeks, significant weight loss, night sweats, or hemoptysis "
            "must undergo sputum microscopy and CBNAAT/TrueNat molecular testing for Rifampicin resistance. "
            "Free diagnostics, daily Fixed-Dose Combination (FDC) anti-TB drugs (HRZE intensive phase 2 months, HRE continuation phase 4 months), "
            "and Nikshay Poshan Yojana nutritional support (Rs 1000/month) are provided at all public health facilities. "
            "Clinicians must notify every TB case on the Nikshay portal and screen household contacts for TB Preventive Therapy (TPT)."
        ),
    },
]


mongo_client: Optional[MongoClient] = None
local_lock = threading.Lock()


def load_local_advisories() -> List[dict]:
    if not os.path.exists(LOCAL_ADVISORIES_PATH):
        with open(LOCAL_ADVISORIES_PATH, "w", encoding="utf-8") as fh:
            json.dump(SEED_ADVISORIES, fh, ensure_ascii=False, indent=2)
        return [dict(doc) for doc in SEED_ADVISORIES]
    with open(LOCAL_ADVISORIES_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_local_advisories(docs: List[dict]) -> None:
    with open(LOCAL_ADVISORIES_PATH, "w", encoding="utf-8") as fh:
        json.dump(docs, fh, ensure_ascii=False, indent=2)


def get_collection():
    global mongo_client
    if mongo_client is None:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo_client[MONGO_DB]
    return db["advisories"]


def seed_advisories_with_retry(max_retries: int = 20, delay_seconds: int = 3) -> None:
    if LOCAL_MODE:
        with local_lock:
            docs = load_local_advisories()
        logger.info(f"Local JSON advisory store ready ({len(docs)} advisories at {LOCAL_ADVISORIES_PATH}).")
        return

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            col = get_collection()
            col.database.client.admin.command("ping")
            col.create_index("advisory_id", unique=True)
            if col.count_documents({}) == 0:
                col.insert_many([dict(doc) for doc in SEED_ADVISORIES])
                logger.info("Seeded 6 WHO and MoHFW/ICMR advisories into MongoDB.")
            else:
                logger.info("Advisories collection already populated.")
            return
        except PyMongoError as exc:
            last_error = exc
            logger.warning(
                f"MongoDB connection attempt {attempt}/{max_retries} failed: {exc}. "
                f"Retrying in {delay_seconds}s..."
            )
            time.sleep(delay_seconds)
    raise RuntimeError(f"Could not connect to MongoDB after {max_retries} attempts: {last_error}")


@app.on_event("startup")
def on_startup():
    seed_advisories_with_retry()


@app.get("/health")
def health_check():
    if LOCAL_MODE:
        with local_lock:
            docs = load_local_advisories()
        return {
            "status": "healthy",
            "service": "advisory-service",
            "database": "json-local",
            "advisory_count": len(docs),
        }
    try:
        col = get_collection()
        col.database.client.admin.command("ping")
        count = col.count_documents({})
        return {
            "status": "healthy",
            "service": "advisory-service",
            "database": "connected",
            "advisory_count": count,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MongoDB unavailable: {exc}")


@app.get("/advisories", response_model=List[AdvisoryResponse])
def list_advisories(
    audience: Optional[str] = Query(default=None, pattern="^(Doctor|Patient|Both)$"),
    category: Optional[str] = Query(default=None),
):
    if LOCAL_MODE:
        with local_lock:
            docs = load_local_advisories()
        if audience:
            if audience in ("Doctor", "Patient"):
                docs = [d for d in docs if d.get("audience") in (audience, "Both")]
            else:
                docs = [d for d in docs if d.get("audience") == "Both"]
        if category:
            cat_lower = category.lower()
            docs = [d for d in docs if cat_lower in d.get("category", "").lower()]
        return docs

    try:
        col = get_collection()
        query = {}
        if audience:
            if audience in ("Doctor", "Patient"):
                query["audience"] = {"$in": [audience, "Both"]}
            else:
                query["audience"] = "Both"
        if category:
            query["category"] = {"$regex": category, "$options": "i"}
        docs = list(col.find(query, {"_id": 0}))
        return docs
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"MongoDB query error: {exc}")


@app.post("/advisories", response_model=AdvisoryResponse, status_code=201)
def create_advisory(payload: AdvisoryCreate):
    if LOCAL_MODE:
        with local_lock:
            docs = load_local_advisories()
            if any(d.get("advisory_id") == payload.advisory_id for d in docs):
                raise HTTPException(status_code=409, detail=f"Advisory {payload.advisory_id} already exists.")
            doc = payload.model_dump()
            docs.append(doc)
            save_local_advisories(docs)
            return doc

    try:
        col = get_collection()
        existing = col.find_one({"advisory_id": payload.advisory_id})
        if existing:
            raise HTTPException(status_code=409, detail=f"Advisory {payload.advisory_id} already exists.")
        doc = payload.model_dump()
        col.insert_one(dict(doc))
        return doc
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(status_code=500, detail=f"MongoDB insert error: {exc}")
