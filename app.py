import streamlit as st
import requests
import json
import time
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
    
    # 1. Aziende
    try:
        r = requests.get(f"{BASE_URL}/Company/Search?$top=120&$orderby=Id desc", headers=crm_headers, timeout=15)
        if r.status_code == 200:
            snapshot["aziende"] = clean_nulls(r.json())
    except Exception:
        pass

    # 2. Lead (inclusi target buy sell)
    try:
        r = requests.get(f"{BASE_URL}/Lead/Search?$top=150&$orderby=Id desc", headers=crm_headers, timeout=15)
        if r.status_code == 200:
            snapshot["lead"] = clean_nulls(r.json())
    except Exception:
        pass

    # 3. Opportunità
    try:
        r = requests.get(f"{BASE_URL}/Opportunity/Search?$top=100&$orderby=Id desc", headers=crm_headers, timeout=15)
        if r.status_code == 200:
            snapshot["opportunita"] = clean_nulls(r.json())
    except Exception:
        pass

    # 4. Attività e verbali recenti
    try:
        r = requests.get(f"{BASE_URL}/Activity/Search?$top=120&$orderby=Id desc", headers=crm_headers, timeout=15)
        if r.status_code == 200:
            snapshot["attivita_recenti"] = clean_nulls(r.json())
    except Exception:
        pass

    return snapshot

# Carica lo snapshot all'avvio
with st.spinner("Sincronizzazione dati dal CRM in corso..."):
    crm_data = get_crm_snapshot()

# --- Chiamata Resiliente a Gemini con Retry ---
def call_gemini(messages_payload: list, model_choice: str):
    last_err = None
    for attempt in range(3):
        try:
            resp = gemini_client.models.generate_content(
                model=model_choice,
                contents=messages_payload
            )
            if resp.text:
                return resp.text
        except Exception as e:
            last_err = e
            err_str = str(e)
            if ("503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            else:
                break
    raise last_err

# --- Barra Laterale e Stato ---
st.title("💼 CRM in Cloud - M&A Advisor")

with st.sidebar:
    st.header("⚙️ Configurazione")
    
    # Selezione esplicita solo dei modelli stabili
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

# --- Istruzioni di Sistema per Flessibilità e Dialogo Attivo ---
system_instruction = f"""
Sei un partner operativo esperto in operazioni M&A e advisory aziendale.
Lavori a fianco dell'utente e hai accesso diretto all'intero database del CRM (aggiornato in memoria):
- Aziende (mandati attivi, clienti formalizzati, target convertite)
- Lead (prospect e target preliminari in fase 'target buy sell')
- Opportunità (match tra cliente mandante e azienda target post-NDA)
- Attività (cronologia delle chiamate, videocall, email archiviate e note operative)

DATI ATTUALI DEL CRM:
{json.dumps(crm_data, ensure_ascii=False)}

LINEE GUIDA DI COMPORTAMENTO:
1. MASSIMA FLESSIBILITÀ LINGUISTICA:
   L'utente ti parlerà in modo naturale, colloquiale o sintetico. Non pretendere una terminologia tecnica o rigida. Interpreta liberamente espressioni come 'in corso', 'chiusa', 'mandati attivi', 'chi stiamo sentendo', 'target buy sell', ecc.
2. TOLLERANZA SUI NOMI (FUZZY MATCH):
   Se l'utente cita solo una parola del nome o un nome parziale (es. 'Rossi' per 'Fratelli Rossi Spa' o 'Acme' per 'Acme Holding'), individua il record corretto senza pretendere la ragione sociale esatta.
3. DOMANDE DI CHIARIMENTO PROATTIVE:
   Se la richiesta dell'utente è generica, ambigua o se esistono più aziende o lead che corrispondono allo stesso termine, NON tirare a indovinare. Spiega cosa hai trovato e poni una domanda diretta e concisa per capire meglio a cosa si riferisce.
4. RIGORE E TRASPARENZA OPERATIVA:
   Quando rispondi a domande su accordi o contatti passati, cita sempre date, interlocutori e punti chiave delle note o delle email. Se un'informazione non compare nei dati del CRM, dillo chiaramente.
"""

# --- Cronologia Messaggi ---
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- Chat Input Standard (Testuale e Veloce) ---
user_query = st.chat_input("Chiedimi qualsiasi cosa sul CRM o su clienti e target...")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        with st.spinner("Analisi in corso..."):
            # Costruzione del payload con cronologia per conversazione naturale
            conversation_payload = [
                {"role": "user", "parts": [{"text": system_instruction + "\n\nIniziamo la conversazione."}]},
                {"role": "model", "parts": [{"text": "Ho esaminato l'archivio completo del CRM. Sono pronto ad assisterti sulle aziende, target, mandati o attività."}]}
            ]
            
            # Aggiungiamo i messaggi precedenti della sessione
            for m in st.session_state.messages:
                role = "user" if m["role"] == "user" else "model"
                conversation_payload.append({
                    "role": role,
                    "parts": [{"text": m["content"]}]
                })

            try:
                answer = call_gemini(conversation_payload, selected_model)
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
            except Exception as e:
                st.error(f"Errore temporaneo: {e}. Riprova tra pochi istanti.")
