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

# --- Configurazione API con Timeout di Rete Rigido ---
CRM_API_KEY = st.secrets["CRMINCLOUD_API_KEY"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
BASE_URL = "https://api.crmincloud.it/api/v1"

# Impostiamo un timeout di 30 secondi per impedire freeze infiniti
gemini_client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(timeout=30.0)
)
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
        r = requests.get(f"{BASE_URL}/Company/Search?$top=120&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["aziende"] = clean_nulls(r.json())
    except Exception:
        pass

    try:
        r = requests.get(f"{BASE_URL}/Lead/Search?$top=150&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["lead"] = clean_nulls(r.json())
    except Exception:
        pass

    try:
        r = requests.get(f"{BASE_URL}/Opportunity/Search?$top=100&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["opportunita"] = clean_nulls(r.json())
    except Exception:
        pass

    try:
        r = requests.get(f"{BASE_URL}/Activity/Search?$top=120&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["attivita_recenti"] = clean_nulls(r.json())
    except Exception:
        pass

    return snapshot

with st.spinner("Sincronizzazione archivio CRM in corso..."):
    crm_data = get_crm_snapshot()

# --- Stopwords Complete Italiane ed Estrazione Entità ---
ITALIAN_STOPWORDS = {
    "a", "ad", "al", "allo", "alla", "ai", "agli", "alle", "all",
    "da", "dal", "dallo", "dalla", "dai", "dagli", "dalle", "dall",
    "di", "del", "dello", "della", "dei", "degli", "delle", "dell",
    "in", "nel", "nello", "nella", "nei", "negli", "nelle", "nell",
    "su", "sul", "sullo", "sulla", "sui", "sugli", "sulle", "sull",
    "con", "col", "coi", "per", "tra", "fra",
    "il", "lo", "la", "i", "gli", "le", "l", "un", "uno", "una",
    "che", "chi", "cui", "quale", "quali", "quanto", "quanti", "quanta", "quante",
    "e", "ed", "o", "od", "se", "ma", "anche", "pure", "non",
    "cosa", "come", "dove", "quando", "perche", "perché",
    "mi", "ti", "ci", "vi", "si", "me", "te", "noi", "voi",
    "ho", "hai", "ha", "abbiamo", "avete", "hanno",
    "sono", "sei", "era", "erano", "stato", "stata", "stati", "state", "siamo",
    "parlato", "colloqui", "colloquio", "call", "riunione", "meeting", "incontro",
    "dici", "dimmi", "trova", "cerca", "mostra", "elenco", "lista", "fatto", "detto"
}

def extract_query_keywords(query: str):
    tokens = re.findall(r"\b[a-zA-Z0-9_]{3,}\b", query.lower())
    return [t for t in tokens if t not in ITALIAN_STOPWORDS]

def matches_entity(item: dict, keywords: list) -> bool:
    text = json.dumps(item, ensure_ascii=False).lower()
    for kw in keywords:
        # Match rigoroso a parola intera per escludere match casuali
        if re.search(r'\b' + re.escape(kw) + r'\b', text):
            return True
    return False

def build_smart_context(query: str, data: dict):
    keywords = extract_query_keywords(query)
    query_lower = query.lower()
    
    is_broad = any(k in query_lower for k in [
        "quante", "quali", "elenco", "tutte", "in corso", "attive", "target", "mandati", "panoramica", "lista", "tutti"
    ])
    
    context = {}

    # Caso 1: Domanda su soggetti specifici (es. Tomware, Alberoni)
    if keywords:
        matched_companies = [c for c in data.get("aziende", []) if matches_entity(c, keywords)]
        matched_leads = [l for l in data.get("lead", []) if matches_entity(l, keywords)]
        matched_opps = [o for o in data.get("opportunita", []) if matches_entity(o, keywords)]
        matched_activities = [a for a in data.get("attivita_recenti", []) if matches_entity(a, keywords)]
        
        if matched_companies or matched_leads or matched_opps or matched_activities:
            context["aziende_pertinenti"] = matched_companies
            context["lead_pertinenti"] = matched_leads
            context["opportunita_pertinenti"] = matched_opps
            context["attivita_e_colloqui_pertinenti"] = matched_activities
            return context

    # Caso 2: Panoramica generale
    if is_broad or not keywords:
        context["tutte_le_aziende"] = data.get("aziende", [])
        context["tutti_i_lead"] = data.get("lead", [])
        context["opportunita"] = data.get("opportunita", [])
        light_acts = []
        for a in data.get("attivita_recenti", [])[:30]:
            light_acts.append({
                "Id": a.get("Id"),
                "Oggetto": a.get("Subject") or a.get("Description"),
                "Data": a.get("Date") or a.get("CreatedDate"),
                "Azienda": a.get("Company") or a.get("CompanyName"),
                "Tipo": a.get("Type")
            })
        context["sintesi_attivita_recenti"] = light_acts
        return context

    return {"info": "Nessuna corrispondenza diretta trovata nel CRM per le parole indicate."}

# --- Chiamata a Gemini con Fallback e Gestione Timeout ---
def call_gemini_safe(prompt_text: str, model_choice: str):
    last_err = None
    for attempt in range(2):
        try:
            resp = gemini_client.models.generate_content(
                model=model_choice,
                contents=prompt_text
            )
            if resp.text:
                return resp.text
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                time.sleep(12)
                continue
            elif "503" in err_str or "UNAVAILABLE" in err_str:
                time.sleep(3)
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
    st.markdown("**Stato Archivio CRM:**")
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
        with st.spinner("Analisi mirata dei dati in memoria..."):
            relevant_context = build_smart_context(user_query, crm_data)

            prompt = f"""
Sei un partner operativo esperto in operazioni M&A e advisory aziendale.
Hai accesso ai dati pertinenti estratti in tempo reale dall'archivio CRM:

DATI ESTRATTI DAL CRM:
{json.dumps(relevant_context, ensure_ascii=False, indent=2)}

REGOLE DI INTERAZIONE:
1. FLESSIBILITÀ: Interpreta liberamente il linguaggio naturale dell'utente (anche se colloquiale).
2. PRECISIONE OPERATIVA: Se trovi riscontri, indica date, interlocutori, argomenti trattati o accordi emersi dalle call/email.
3. CHIAREZZA E CHIARIMENTI: Se i dati su Tomware o Alberoni contengono dettagli parziali o se non trovi note specifiche tra le due parti, spiegate esattamente cosa risulta a sistema e formula una domanda di chiarimento.

DOMANDA:
{user_query}
"""
            try:
                answer = call_gemini_safe(prompt, selected_model)
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
            except Exception as e:
                st.error(f"Errore temporaneo durante la generazione: {e}. Attendi qualche secondo e riprova.")

            with st.expander("🔍 Dati pertinenti estratti"):
                st.json(relevant_context)
