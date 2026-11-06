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

# --- Chiamata a Gemini con Retry automatico su 503 ---
def call_gemini_robust(prompt: str, model_name: str = "gemini-3.8-flash", max_retries: int = 3):
    last_err = None
    for attempt in range(max_retries):
        try:
            resp = gemini_client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            return resp.text
        except Exception as e:
            last_err = e
            err_str = str(e)
            # Se Google è momentaneamente saturo (503 / 429), aspetta e riprova
            if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str:
                time.sleep(2 * (attempt + 1))
                continue
            else:
                break
    raise last_err

# --- Funzioni di Interrogazione CRM in Cloud ---
def get_recent_entities(entity_type: str, top: int = 50):
    try:
        url = f"{BASE_URL}/{entity_type}/Search?$top={top}&$orderby=Id desc"
        resp = requests.get(url, headers=crm_headers, timeout=10)
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
    st.info("● Modello: Gemini 3.8 Flash (Auto-Retry)")
    st.markdown("---")
    st.markdown("**Regole Operative M&A:**")
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
        with st.spinner("Consultazione del CRM in corso..."):
            context_data = {}
            query_lower = user_query.lower()

            # Rilevamento intenti: panoramica generale o ricerca specifica
            is_general_query = any(k in query_lower for k in [
                "quante", "quali", "elenco", "tutte", "in corso", "attive", "target", "mandati"
            ])

            if is_general_query:
                # Estraiamo le aziende, opportunità e lead recenti
                context_data["aziende_recenti"] = get_recent_entities("Company", top=50)
                context_data["opportunita_recenti"] = get_recent_entities("Opportunity", top=50)
                context_data["target_leads"] = get_target_leads()

            # Cerchiamo anche per parole chiave estratte dalla domanda
            words = [w for w in user_query.replace("?", "").replace(",", "").split() if len(w) > 3 and w.lower() not in [
                "quante", "quali", "abbiamo", "questo", "momento", "corso", "aziende", "cliente", "lead"
            ]]
            
            for w in words[:2]:
                search_res = search_crm_entities(w)
                if search_res["companies"] or search_res["leads"]:
                    context_data[f"ricerca_{w}"] = search_res
                    for c in search_res.get("companies", [])[:2]:
                        c_id = c.get("Id") or c.get("id")
                        if c_id:
                            context_data[f"attivita_{c.get('Name')}"] = get_activities("Company", c_id)
                    for l in search_res.get("leads", [])[:2]:
                        l_id = l.get("Id") or l.get("id")
                        if l_id:
                            context_data[f"attivita_{l.get('Company') or l.get('Subject')}"] = get_activities("Lead", l_id)

            answer_prompt = f"""
            Sei un assistente specializzato in operazioni M&A e advisory aziendale.
            Hai accesso ai dati in tempo reale estratti tramite API da 'CRM in Cloud'.
            
            Logica operativa del CRM:
            - Le aziende entrano come Lead sia per incarichi buy/sell generali sia per fare matching.
            - La fase 'target buy sell' identifica le aziende target proposte per un match.
            - Diventano 'Azienda' (Company) quando c'è un mandato formale o un NDA firmato.
            - L'oggetto 'Opportunità' unisce il cliente mandante con l'azienda target dopo l'NDA.
            - Nelle attività (Activities) sono registrati verbali di call, note ed email archiviate.

            Dati estratti dal CRM:
            {json.dumps(context_data, ensure_ascii=False, indent=2)}

            Domanda dell'utente:
            {user_query}

            Fornisci una risposta chiara, professionale e operativa. 
            Se trovi aziende o lead rilevanti, elencali con il loro stato. 
            Se i dati estratti non contengono informazioni sufficienti, segnalalo spiegando cosa risulta vuoto.
            """

            try:
                answer = call_gemini_robust(answer_prompt, model_name="gemini-3.8-flash")
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
            except Exception as e:
                st.error(f"Errore temporaneo nei servizi Google: {e}. Riprova tra pochi istanti.")

            with st.expander("🔍 Dati grezzi estratti dal CRM"):
                st.json(context_data)
