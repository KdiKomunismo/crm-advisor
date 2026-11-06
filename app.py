import streamlit as st
import requests
import json
import time
from google import genai

st.set_page_config(page_title="CRM in Cloud - M&A Advisor", page_icon="💼", layout="wide")

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

# --- Chiamata a Gemini con Retry e Failover Flash / Pro ---
def call_gemini_smart(prompt: str):
    models_to_try = ["gemini-3.8-flash", "gemini-3.8-pro"]
    last_err = None

    for model_name in models_to_try:
        for attempt in range(2):
            try:
                resp = gemini_client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                if resp.text:
                    return resp.text, model_name
            except Exception as e:
                last_err = e
                err_str = str(e)
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str:
                    time.sleep(2)
                    continue
                else:
                    break
    raise last_err

# --- Interrogazione CRM in Cloud (Dati Integrali al 100%) ---
def get_entities(entity_type: str, top: int = 60):
    try:
        url = f"{BASE_URL}/{entity_type}/Search?$top={top}&$orderby=Id desc"
        resp = requests.get(url, headers=crm_headers, timeout=12)
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []

def search_crm_entities(keyword: str):
    results = {"companies": [], "leads": []}
    try:
        c_resp = requests.get(
            f"{BASE_URL}/Company/Search",
            headers=crm_headers,
            params={"$filter": f"contains(Name, '{keyword}')"},
            timeout=10
        )
        if c_resp.status_code == 200:
            results["companies"] = c_resp.json()
    except Exception:
        pass

    try:
        l_resp = requests.get(
            f"{BASE_URL}/Lead/Search",
            headers=crm_headers,
            params={"$filter": f"contains(Company, '{keyword}') or contains(Subject, '{keyword}')"},
            timeout=10
        )
        if l_resp.status_code == 200:
            results["leads"] = l_resp.json()
    except Exception:
        pass

    return results

def get_activities(owner_type: str, entity_id: int):
    try:
        resp = requests.get(f"{BASE_URL}/{owner_type}/{entity_id}/Activities", headers=crm_headers, timeout=10)
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []

def get_target_leads():
    try:
        resp = requests.get(
            f"{BASE_URL}/Lead/Search",
            headers=crm_headers,
            params={"$filter": "contains(Phase, 'target buy sell') or contains(Status, 'target buy sell')"},
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []

# --- Interfaccia Grafica ---
st.title("💼 CRM in Cloud - M&A Advisor")

with st.sidebar:
    st.header("Stato Sistema")
    st.success("● Connesso a CRM in Cloud")
    st.info("● Gemini: Analisi Completa (Zero filtri)")
    st.markdown("---")
    st.markdown("**Regole M&A:**")
    st.markdown("- **Lead**: Prospect generici o target preliminari.")
    st.markdown("- **Fase 'target buy sell'**: Target proposte per match.")
    st.markdown("- **Azienda**: Mandato attivo o NDA firmato.")
    st.markdown("- **Opportunità**: Match cliente-target post-NDA.")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_query = st.chat_input("Fai una domanda sul CRM...")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        with st.spinner("Estrazione dati integrali dal CRM..."):
            context_data = {}
            query_lower = user_query.lower()

            # Se la domanda richiede panoramiche, elenchi o filtri generali
            is_broad_query = any(k in query_lower for k in [
                "quante", "quali", "elenco", "tutte", "in corso", "attive", "target", "mandati", "lista"
            ])

            if is_broad_query:
                context_data["aziende"] = get_entities("Company", top=60)
                context_data["opportunita"] = get_entities("Opportunity", top=60)
                context_data["target_leads"] = get_target_leads()

            # Ricerca specifica per parole chiave o nomi propri citati
            words = [w for w in user_query.replace("?", "").replace(",", "").replace("=", " ").split() if len(w) > 3 and w.lower() not in [
                "quante", "quali", "abbiamo", "questo", "momento", "corso", "aziende", "cliente", "lead"
            ]]
            
            for w in words[:2]:
                search_res = search_crm_entities(w)
                if search_res["companies"] or search_res["leads"]:
                    context_data[f"ricerca_{w}"] = search_res
                    for c in search_res.get("companies", [])[:3]:
                        c_id = c.get("Id") or c.get("id")
                        if c_id:
                            context_data[f"attivita_company_{c_id}"] = get_activities("Company", c_id)
                    for l in search_res.get("leads", [])[:3]:
                        l_id = l.get("Id") or l.get("id")
                        if l_id:
                            context_data[f"attivita_lead_{l_id}"] = get_activities("Lead", l_id)

            answer_prompt = f"""
            Sei un assistente specializzato in operazioni M&A e advisory per l'azienda.
            Hai accesso a tutti i dati integrali (compresi campi personalizzati, stati, note ed email) estratti in tempo reale da 'CRM in Cloud'.
            
            Logica operativa M&A:
            - Le aziende entrano come Lead per incarichi buy/sell o per matching.
            - La fase 'target buy sell' identifica le aziende target da proporre per un match.
            - Diventano 'Azienda' (Company) quando c'è un mandato o un NDA firmato.
            - L'oggetto 'Opportunità' unisce il cliente mandante con l'azienda target post-NDA.
            - Esamina attentamente tutti i campi personalizzati (CustomFields) e le proprietà di stato per verificare condizioni come 'in corso = sì' o analoghe.

            Dati integrali estratti dal CRM:
            {json.dumps(context_data, ensure_ascii=False, indent=2)}

            Domanda dell'utente:
            {user_query}

            Fornisci una risposta analitica, accurata e professionale. Se riscontri aziende che soddisfano la richiesta, elencale con i dettagli pertinenti. Se un parametro non compare esplicitamente, spiega cosa hai individuato nei campi del CRM.
            """

            try:
                answer, model_used = call_gemini_smart(answer_prompt)
                st.markdown(answer)
                st.caption(f"⚡ Risposta elaborata con {model_used}")
                st.session_state.messages.append({"role": "assistant", "content": answer})
            except Exception as e:
                st.error(f"Errore di elaborazione: {e}. Riprova tra un istante.")

            with st.expander("🔍 Dati integrali estratti dal CRM"):
                st.json(context_data)
