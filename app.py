import streamlit as st
import requests
import json
import time
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from google import genai

st.set_page_config(page_title="CRM M&A Advisor", page_icon="💼", layout="wide")

# --- Protezione con Password ---
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.markdown("### 🔒 Accesso Riservato M&A CRM Assistant")
    pwd = st.text_input("Inserisci la password di accesso aziendale:", type="password")
    if st.button("Accedi"):
        configured_pwd = st.secrets.get("APP_PASSWORD", "mediazienda2025")
        if pwd == configured_pwd:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Password errata. Riprova.")
    return False

if not check_password():
    st.stop()

# --- Configurazione API ---
CRM_API_KEY = st.secrets["CRMINCLOUD_API_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
BASE_URL = "https://api.crmincloud.it/api/v1"

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
crm_headers = {
    "apikey": CRM_API_KEY,
    "Accept": "application/json"
}

# --- Pulizia HTML e Testo ---
def clean_html_text(text: str) -> str:
    if not isinstance(text, str):
        return text
    text = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def clean_record(obj):
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if v is not None and v != "" and v != [] and v != {}:
                if k.lower() in ["body", "htmlbody", "textbody", "description", "note"] and isinstance(v, str):
                    cleaned[k] = clean_html_text(v)
                else:
                    cleaned[k] = clean_record(v)
        return cleaned
    elif isinstance(obj, list):
        return [clean_record(elem) for elem in obj if elem is not None and elem != ""]
    return obj

# --- Rilevamento Flessibile delle Email (Case-Insensitive) ---
def is_email_smart(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    
    ci = {str(k).lower(): v for k, v in item.items()}
    
    # 1. Controllo tipologia o canale attività
    type_val = str(ci.get("type") or ci.get("activitytype") or ci.get("typename") or ci.get("category") or ci.get("channel") or "").lower()
    if any(x in type_val for x in ["email", "mail", "posta", "pec", "message"]):
        return True
        
    # 2. Controllo mittenti, destinatari e direzione
    if any(k in ci for k in ["from", "to", "sender", "recipient", "recipients", "senderemail", "recipientemail", "messageid"]):
        return True
    if "direction" in ci and ci["direction"] is not None:
        # Se c'è una direzione o un indirizzo email nei campi
        return True

    # 3. Controllo oggetto tipico di corrispondenza
    subj = str(ci.get("subject") or ci.get("title") or "").lower()
    if any(subj.startswith(prefix) for prefix in ["re:", "fwd:", "r:", "i:", "fw:"]):
        return True
    if any(x in subj for x in ["email", " mail", "posta elettronica"]):
        return True

    # 4. Controllo presenza di intestazioni email nella descrizione o note
    desc = str(ci.get("description") or ci.get("body") or ci.get("note") or "").lower()
    if ("da:" in desc and "a:" in desc) or ("from:" in desc and "to:" in desc):
        return True
    if "@" in str(ci.get("from") or ci.get("to") or ""):
        return True

    return False

# --- Paginazione Profonda e Caricamento Snapshot CRM ---
@st.cache_data(ttl=1800, show_spinner=False)
def get_crm_snapshot():
    snapshot = {
        "timestamp": datetime.now(ZoneInfo("Europe/Rome")).strftime("%H:%M:%S"),
        "aziende": [],
        "contatti": [],
        "lead": [],
        "opportunita": [],
        "attivita_recenti": [],
        "email_archiviate": []
    }
    
    # 1. Aziende (fino a 200)
    try:
        r = requests.get(f"{BASE_URL}/Company/Search?$top=200&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["aziende"] = clean_record(r.json())
    except Exception:
        pass

    # 2. Contatti (Persone fisiche, manager, interlocutori)
    try:
        r = requests.get(f"{BASE_URL}/Contact/Search?$top=200&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["contatti"] = clean_record(r.json())
    except Exception:
        pass

    # 3. Lead (fino a 200)
    try:
        r = requests.get(f"{BASE_URL}/Lead/Search?$top=200&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["lead"] = clean_record(r.json())
    except Exception:
        pass

    # 4. Opportunità
    try:
        r = requests.get(f"{BASE_URL}/Opportunity/Search?$top=100&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["opportunita"] = clean_record(r.json())
    except Exception:
        pass

    # 5. Scansione profonda attività (fino a 800 record = 8 pagine)
    all_raw_activities = []
    for page in range(8):
        skip = page * 100
        try:
            r = requests.get(f"{BASE_URL}/Activity/Search?$top=100&$skip={skip}&$orderby=Id desc", headers=crm_headers, timeout=10)
            if r.status_code == 200:
                batch = r.json()
                if not batch:
                    break
                all_raw_activities.extend(batch)
                if len(batch) < 100:
                    break
            else:
                break
        except Exception:
            break

    # Tentativo di recupero anche da endpoint dedicati se abilitati sul server
    dedicated_emails = []
    for ep in [f"{BASE_URL}/Email/Search?$top=100",
