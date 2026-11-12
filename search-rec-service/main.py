import os
import json
import logging
import threading
from typing import Dict, List, Optional
import numpy as np
import faiss
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("search-rec-service")

ADVISORY_SVC_URL = os.getenv("ADVISORY_SVC_URL", "http://advisory-service:8002/advisories")
INDEX_PATH = os.getenv("INDEX_PATH", "advisories.index")
META_PATH = os.getenv("META_PATH", "advisories_meta.json")
MODEL_NAME = "all-MiniLM-L6-v2"

FALLBACK_ADVISORIES = [
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

app = FastAPI(
    title="MedPulse AI - Semantic Search & Vector Recommendation Service",
    description="FAISS + SentenceTransformers semantic search and related-advisory recommendation engine.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

index_lock = threading.Lock()
model: Optional[SentenceTransformer] = None
faiss_index: Optional[faiss.IndexFlatIP] = None
advisories_store: List[Dict] = []
advisory_vectors: Optional[np.ndarray] = None
data_source_mode: str = "uninitialized"


def advisory_to_text(adv: Dict) -> str:
    symptoms = adv.get("symptoms", [])
    if isinstance(symptoms, list):
        symptoms_text = ", ".join(symptoms)
    else:
        symptoms_text = str(symptoms)
    return (
        f"Title: {adv.get('title', '')}. "
        f"Source: {adv.get('source', '')}. "
        f"Category: {adv.get('category', '')}. "
        f"Audience: {adv.get('audience', 'Both')}. "
        f"Symptoms: {symptoms_text}. "
        f"Guidance: {adv.get('content', '')}"
    )


def fetch_advisories_with_fallback() -> tuple[List[Dict], str]:
    try:
        resp = requests.get(ADVISORY_SVC_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list) and len(data) > 0:
            logger.info(f"Fetched {len(data)} advisories from {ADVISORY_SVC_URL}.")
            return data, "advisory-service"
    except Exception as exc:
        logger.warning(f"Could not fetch advisories from {ADVISORY_SVC_URL}: {exc}. Using fallback dataset.")
    return [dict(item) for item in FALLBACK_ADVISORIES], "fallback"


def build_and_persist_index(advisories: List[Dict], source_label: str) -> int:
    global faiss_index, advisories_store, advisory_vectors, data_source_mode
    if model is None:
        raise RuntimeError("Embedding model not loaded yet.")

    texts = [advisory_to_text(adv) for adv in advisories]
    embeddings = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    embeddings = np.asarray(embeddings, dtype="float32")
    faiss.normalize_L2(embeddings)

    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)

    faiss.write_index(index, INDEX_PATH)
    with open(META_PATH, "w", encoding="utf-8") as fh:
        json.dump(advisories, fh, ensure_ascii=False, indent=2)

    with index_lock:
        faiss_index = index
        advisories_store = advisories
        advisory_vectors = embeddings
        data_source_mode = source_label

    logger.info(f"Built & persisted FAISS IndexFlatIP ({len(advisories)} vectors, dim={dim}) to {INDEX_PATH}.")
    return len(advisories)


def matches_role(advisory: Dict, role: str) -> bool:
    if role == "All":
        return True
    audience = advisory.get("audience", "Both")
    return audience in (role, "Both")


@app.on_event("startup")
def on_startup():
    global model
    logger.info(f"Loading SentenceTransformer model: {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)
    advisories, source_label = fetch_advisories_with_fallback()
    build_and_persist_index(advisories, source_label)


@app.get("/health")
def health_check():
    return {
        "status": "healthy" if faiss_index is not None else "initializing",
        "service": "search-rec-service",
        "model": MODEL_NAME,
        "indexed_advisories": len(advisories_store),
        "index_file": INDEX_PATH,
        "data_source": data_source_mode,
    }


@app.post("/reindex")
def reindex():
    try:
        resp = requests.get(ADVISORY_SVC_URL, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list) or len(data) == 0:
            raise HTTPException(status_code=502, detail="Advisory service returned an empty list.")
        count = build_and_persist_index(data, "advisory-service")
        return {
            "status": "reindexed",
            "indexed_advisories": count,
            "index_file": INDEX_PATH,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reindex from advisory-service: {exc}")


@app.get("/search")
def search_advisories(
    q: str = Query(..., min_length=1, description="Symptom or clinical query"),
    role: str = Query(default="All", pattern="^(All|Doctor|Patient)$"),
    top_k: int = Query(default=3, ge=1, le=20),
):
    if model is None or faiss_index is None:
        raise HTTPException(status_code=503, detail="Search index not ready.")

    with index_lock:
        index = faiss_index
        store = list(advisories_store)
        vectors = advisory_vectors

    total_docs = len(store)
    if total_docs == 0:
        return {"query": q, "role": role, "results": [], "recommendations": []}

    query_vec = model.encode([q], convert_to_numpy=True, show_progress_bar=False)
    query_vec = np.asarray(query_vec, dtype="float32")
    faiss.normalize_L2(query_vec)

    scores, indices = index.search(query_vec, total_docs)

    results = []
    matched_ids = set()
    top_matched_idx = None

    for score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= total_docs:
            continue
        adv = store[idx]
        if not matches_role(adv, role):
            continue
        if top_matched_idx is None:
            top_matched_idx = int(idx)
        entry = dict(adv)
        entry["similarity_score"] = round(float(score), 4)
        results.append(entry)
        matched_ids.add(adv.get("advisory_id"))
        if len(results) >= top_k:
            break

    recommendations = []
    if top_matched_idx is not None and vectors is not None:
        anchor_vec = vectors[top_matched_idx : top_matched_idx + 1]
        rec_scores, rec_indices = index.search(anchor_vec, total_docs)
        for rec_score, rec_idx in zip(rec_scores[0], rec_indices[0]):
            if rec_idx < 0 or rec_idx >= total_docs:
                continue
            adv = store[rec_idx]
            if adv.get("advisory_id") in matched_ids:
                continue
            if not matches_role(adv, role):
                continue
            rec_entry = dict(adv)
            rec_entry["similarity_score"] = round(float(rec_score), 4)
            rec_entry["recommended_because"] = store[top_matched_idx].get("title", "")
            recommendations.append(rec_entry)
            if len(recommendations) >= 2:
                break

    return {
        "query": q,
        "role": role,
        "data_source": data_source_mode,
        "results": results,
        "recommendations": recommendations,
    }
