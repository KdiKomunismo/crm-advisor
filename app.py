import streamlit as st
import requests
import json
import time
import re
from datetime import datetime
from google import genai
from google.genai import types

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

# --- Pulizia Valori Nulli (Lossless) ---
def clean_nulls(obj):
    if isinstance(obj, dict):
        return {k: clean_nulls(v) for k, v in obj.items() if v is not None and v != "" and v != [] and v != {}}
    elif isinstance(obj, list):
        return [clean_nulls(elem) for elem in obj if elem is not None and elem != ""]
    return obj

# --- Caricamento Snapshot CRM con Cache a 30 Minuti ---
@st.cache_data(ttl=1800, show_spinner=False)
def get_crm_snapshot():
    snapshot = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "aziende": [],
        "lead": [],
        "opportunita": [],
        "attivita_recenti": []
    }
    
    try:
        r = requests.get(f"{BASE_URL}/Company/Search?$top=120&$orderby=Id desc", headers=crm_headers, timeout=15)
        if r.status_code == 200:
            snapshot["aziende"] = clean_nulls(r.json())
    except Exception:
        pass

    try:
        r = requests.get(f"{BASE_URL}/Lead/Search?$top=150&$orderby=Id desc", headers=crm_headers, timeout=15)
        if r.status_code == 200:
            snapshot["lead"] = clean_nulls(r.json())
    except Exception:
        pass

    try:
        r = requests.get(f"{BASE_URL}/Opportunity/Search?$top=100&$orderby=Id desc", headers=crm_headers, timeout=15)
        if r.status_code == 200:
            snapshot["opportunita"] = clean_nulls(r.json())
    except Exception:
        pass

    try:
        r = requests.get(f"{BASE_URL}/Activity/Search?$top=120&$orderby=Id desc", headers=crm_headers, timeout=15)
        if r.status_code == 200:
            snapshot["attivita_recenti"] = clean_nulls(r.json())
    except Exception:
        pass

    return snapshot

with st.spinner("Sincronizzazione dati dal CRM in corso..."):
    crm_data = get_crm_snapshot()

# --- Filtro Intelligente in RAM (Anti-Quota 429) ---
STOPWORDS = {
    "cosa", "abbiamo", "parlato", "colloqui", "colloquio", "detto", "fatto", "call", "riunione",
    "incontro", "meeting", "email", "mail", "dici", "dimmi", "quali", "quante", "tutti", "tutte",
    "sono", "della", "delle", "degli", "dello", "nell", "nella", "nelle", "negli", "questo", "questa"
}

def extract_query_keywords(query: str):
    tokens = re.findall(r"\b[a-zA-Z0-9_]{3,}\b", query.lower())
    return [t for t in tokens if t not in STOPWORDS]

def build_smart_context(query: str, data: dict):
    keywords = extract_query_keywords(query)
    query_lower = query.lower()
    
    is_broad = any(k in query_lower for k in [
        "quante", "quali", "elenco", "tutte", "in corso", "attive", "target", "mandati", "panoramica", "lista"
    ])
    
    context = {}

    # Caso 1: Ricerca specifica su entità citate (es. Tomware, Alberoni)
    if keywords:
        matched_companies = [
            c for c in data.get("aziende", [])
            if any(kw in json.dumps(c, ensure_ascii=False).lower() for kw in keywords)
        ]
        matched_leads = [
            l for l in data.get("lead", [])
            if any(kw in json.dumps(l, ensure_ascii=False).lower() for kw in keywords)
        ]
        matched_opps = [
            o for o in data.get("opportunita", [])
            if any(kw in json.dumps(o, ensure_ascii=False).lower() for kw in keywords)
        ]
        matched_activities = [
            a for a in data.get("attivita_recenti", [])
            if any(kw in json.dumps(a, ensure_ascii=False).lower() for kw in keywords)
        ]
        
        if matched_companies or matched_leads or matched_opps or matched_activities:
            context["aziende_pertinenti"] = matched_companies
            context["lead_pertinenti"] = matched_leads
            context["opportunita_pertinenti"] = matched_opps
            context["attivita_e_colloqui_pertinenti"] = matched_activities
            return context

    # Caso 2: Panoramica generale (aziende, target o mandati)
    if is_broad or not keywords:
        # Per le aziende e i lead manteniamo tutti i dati e i campi custom integrali
        context["tutte_le_aziende"] = data.get("aziende", [])
        context["tutti_i_lead"] = data.get("lead", [])
        context["opportunita"] = data.get("opportunita", [])
        # Nelle attività generali sintetizziamo oggetto, tipo e azienda per contenere i token
        light_activities = []
        for a in data.get("attivita_recenti", [])[:40]:
            light_activities.append({
                "Id": a.get("Id"),
                "Oggetto": a.get("Subject") or a.get("Description"),
                "Data": a.get("Date") or a.get("CreatedDate"),
                "Azienda": a.get("Company") or a.get("CompanyName"),
                "Tipo": a.get("Type")
            })
        context["sintesi_attivita_recenti"] = light_activities
        return context

    # Fallback se non ci sono match diretti: elenco nomi e stati per disambiguazione
    context["elenco_aziende_crm"] = [
        {"Id": c.get("Id"), "Nome": c.get("Name"), "Stato": c.get("State") or c.get("Status")}
        for c in data.get("aziende", [])
    ]
    context["elenco_lead_crm"] = [
        {"Id": l.get("Id"), "Nome": l.get("Company") or l.get("Subject"), "Fase": l.get("Phase")}
        for l in data.get("lead", [])
    ]
    return context

# --- Chiamata Resiliente a Gemini con Auto-Retry su Quota/503 ---
def call_gemini_safe(contents_payload: list, model_choice: str):
    last_err = None
    for attempt in range(3):
        try:
            resp = gemini_client.models.generate_content(
                model=model_choice,
                contents=contents_payload
            )
            if resp.text:
                return resp.text
        except Exception as e:
            last_err = e
            err_str = str(e)
            # Gestione picchi di carico (503) o rate limit temporaneo (429)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                time.sleep(13)  # Attende la finestra del rate limit
                continue
            elif "503" in err_str or "UNAVAILABLE" in err_str:
                time.sleep(3 * (attempt + 1))
                continue
            else:
                break
    raise last_err

# --- Barra Laterale ---
st.title("💼 CRM in Cloud - M&A Advisor")

with st.sidebar:
    st.header("⚙️ Configurazione")
    
    selected_model = st.selectbox(
        "Modello Gemini:",
        options=["gemini-3.8-flash", "gemini-3.5-flash"],
        index=0
    )
    
    st.markdown("---")
    st.markdown(f"**Stato Archivio CRM:**")
    st.markdown(f"- Ultimo sync: `{crm_data.get('timestamp')}`")
    st.markdown(f"- Aziende: **{len(crm_data.get('aziende', []))}**")
    st.markdown(f"- Lead: **{len(crm_data.get('lead', []))}**")
    st.markdown(f"- Opportunità: **{len(crm_data.get('opportunita', []))}**")
    st.markdown(f"- Attività/Note: **{len(crm_data.get('attivita_recenti', []))}**")
    
    if st.button("🔄 Sincronizza CRM Adesso"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    if st.button("🗑️ Cancella Conversazione"):
        st.session_state.messages = []
        st.rerun()

# --- Cronologia Messaggi ---
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_query = st.chat_input("Chiedimi qualsiasi cosa sul CRM o su clienti e target...")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        with st.spinner("Analisi dei dati in memoria..."):
            relevant_context = build_smart_context(user_query, crm_data)

            prompt = f"""
Sei un partner operativo esperto in M&A e advisory aziendale.
Lavori a fianco dell'utente e rispondi sfruttando i dati reali estratti da 'CRM in Cloud'.

DATI ESTRATTI E PERTINENTI ALLA RICHIESTA:
{json.dumps(relevant_context, ensure_ascii=False, indent=2)}

REGOLE DI INTERAZIONE:
1. FLESSIBILITÀ LINGUISTICA TOTALE:
   L'utente usa espressioni naturali, colloquiali o sintetiche. Non pretendere una terminologia tecnica o rigida. Interpreta liberamente espressioni come 'colloqui', 'in corso', 'mandati attivi', 'target buy sell'.
2. PRECISIONE OPERATIVA:
   Cita date, interlocutori e punti chiave emersi dalle call, email o note di contatto se presenti.
3. DOMANDE PROATTIVE DI CHIARIMENTO:
   Se i dati estratti non sono sufficienti per rispondere con certezza o se noti omonimie/ambiguità, NON inventare nulla: spiega cosa hai trovato e poni direttamente una domanda mirata per capire a cosa si riferisce.

DOMANDA DELL'UTENTE:
{user_query}
"""
            payload = [{"role": "user", "parts": [{"text": prompt}]}]

            try:
                answer = call_gemini_safe(payload, selected_model)
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
            except Exception as e:
                st.error(f"Errore temporaneo nei servizi Google: {e}. Attendi qualche secondo e riprova.")

            with st.expander("🔍 Dati contestuali utilizzati"):
                st.json(relevant_context)
