import streamlit as st
import requests
import json
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

# --- Funzioni di Interrogazione CRM ---
def search_crm_entities(keyword: str):
    results = {"companies": [], "leads": []}
    try:
        c_resp = requests.get(f"{BASE_URL}/Company/Search", headers=crm_headers, params={"$filter": f"contains(Name, '{keyword}')"}, timeout=10)
        if c_resp.status_code == 200:
            results["companies"] = c_resp.json()
    except Exception:
        pass

    try:
        l_resp = requests.get(f"{BASE_URL}/Lead/Search", headers=crm_headers, params={"$filter": f"contains(Company, '{keyword}') or contains(Subject, '{keyword}')"}, timeout=10)
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
        resp = requests.get(f"{BASE_URL}/Lead/Search", headers=crm_headers, params={"$filter": "contains(Phase, 'target buy sell') or contains(Status, 'target buy sell')"}, timeout=10)
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
    st.info("● Modello: Gemini 3.8 Flash")
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
        with st.spinner("Consultazione del CRM in corso..."):
            extract_prompt = f"""
            Dalla seguente domanda dell'utente, estrai il nome dell'azienda o del cliente menzionato.
            Se l'utente non cita un'azienda specifica ma chiede una lista generale o una panoramica, rispondi SOLO con 'ALL'.
            Restituisci SOLO il nome o 'ALL', senza commenti né punteggiatura aggiuntiva.
            Domanda: {user_query}
            """
            try:
                extraction_resp = gemini_client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=extract_prompt
                )
                extraction = extraction_resp.text.strip().replace("'", "").replace('"', "")
            except Exception:
                extraction = "ALL"

            context_data = {}
            if extraction == "ALL" or "target" in user_query.lower() or "aziende" in user_query.lower() or "in corso" in user_query.lower():
                context_data["target_leads_in_cloud"] = get_target_leads()

            if extraction != "ALL" and len(extraction) > 1:
                search_res = search_crm_entities(extraction)
                context_data["search_results"] = search_res

                activities_list = []
                for c in search_res.get("companies", []):
                    c_id = c.get("Id") or c.get("id")
                    if c_id:
                        acts = get_activities("Company", c_id)
                        activities_list.append({"company": c.get("Name"), "activities": acts})

                for l in search_res.get("leads", []):
                    l_id = l.get("Id") or l.get("id")
                    if l_id:
                        acts = get_activities("Lead", l_id)
                        activities_list.append({"lead": l.get("Company") or l.get("Subject"), "activities": acts})

                context_data["activities_and_emails"] = activities_list

            answer_prompt = f"""
            Sei un assistente specializzato in operazioni M&A e advisory aziendale.
            Hai accesso ai dati in tempo reale estratti tramite API da 'CRM in Cloud'.
            
            Logica operativa del CRM:
            - Le aziende entrano come Lead sia per incarichi buy/sell generali sia per fare matching.
            - La fase 'target buy sell' identifica le aziende target proposte per un match.
            - Diventano 'Azienda' (Company) quando c'è mandato formale o NDA firmato.
            - L'oggetto 'Opportunità' unisce il cliente mandante con l'azienda target dopo l'NDA.
            - Nelle attività (Activities) sono registrati verbali di call, note ed email archiviate.

            Dati estratti dal CRM:
            {json.dumps(context_data, ensure_ascii=False, indent=2)}

            Domanda dell'utente:
            {user_query}

            Rispondi in modo chiaro e operativo citando date, interlocutori e punti chiave se presenti. Se il blocco dati restituito è vuoto o non contiene riscontri specifici, indicalo chiaramente.
            """

            try:
                resp = gemini_client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=answer_prompt
                )
                st.markdown(resp.text)
                st.session_state.messages.append({"role": "assistant", "content": resp.text})
            except Exception as e:
                st.error(f"Errore di comunicazione con Gemini: {e}")

            with st.expander("🔍 Dati grezzi estratti dal CRM"):
                st.json(context_data)
