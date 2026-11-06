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

# --- Rilevamento Dinamico dei Modelli Abilitati ---
@st.cache_data(ttl=1800)
def get_account_models():
    models = []
    try:
        for m in gemini_client.models.list():
            name = getattr(m, "name", "").replace("models/", "")
            # Seleziona i modelli testuali/multimodali Gemini escludendo embedding e video
            if "gemini" in name.lower() and not any(x in name.lower() for x in ["embed", "imagen", "veo", "aqa"]):
                models.append(name)
    except Exception:
        pass
    if not models:
        models = ["gemini-3.8-flash"]
    return models

available_models = get_account_models()

# --- Pulizia Lossless (Zero Perdita di Informazioni) ---
def remove_empty_keys(obj):
    """Rimuove solo valori nulli o stringhe vuote, preservando integralmente tutti i campi e dati custom valorizzati."""
    if isinstance(obj, dict):
        return {k: remove_empty_keys(v) for k, v in obj.items() if v is not None and v != "" and v != [] and v != {}}
    elif isinstance(obj, list):
        return [remove_empty_keys(elem) for elem in obj if elem is not None and elem != ""]
    return obj

# --- Chiamata Resiliente con Failover ---
def call_gemini_smart(prompt: str, chosen_model: str):
    # Prova prima il modello scelto dall'utente, poi gli altri disponibili
    models_to_attempt = [chosen_model] + [m for m in available_models if m != chosen_model]
    last_err = None

    for m_name in models_to_attempt:
        for attempt in range(3):
            try:
                resp = gemini_client.models.generate_content(
                    model=m_name,
                    contents=prompt
                )
                if resp.text:
                    return resp.text, m_name
            except Exception as e:
                last_err = e
                err_str = str(e)
                # Se il modello ha picchi di traffico (503 / 429), attende progressivamente
                if ("503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str) and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                else:
                    break
    raise last_err

# --- Interrogazione CRM in Cloud ---
def get_entities(entity_type: str, top: int = 50):
    try:
        url = f"{BASE_URL}/{entity_type}/Search?$top={top}&$orderby=Id desc"
        resp = requests.get(url, headers=crm_headers, timeout=12)
        if resp.status_code == 200:
            return remove_empty_keys(resp.json())
        return []
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
            results["companies"] = remove_empty_keys(c_resp.json())
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
            results["leads"] = remove_empty_keys(l_resp.json())
    except Exception:
        pass

    return results

def get_activities(owner_type: str, entity_id: int):
    try:
        resp = requests.get(f"{BASE_URL}/{owner_type}/{entity_id}/Activities", headers=crm_headers, timeout=10)
        if resp.status_code == 200:
            return remove_empty_keys(resp.json())
        return []
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
            return remove_empty_keys(resp.json())
    except Exception:
        pass
    return []

# --- Interfaccia Grafica ---
st.title("💼 CRM in Cloud - M&A Advisor")

with st.sidebar:
    st.header("Stato Sistema")
    st.success("● Connesso a CRM in Cloud")
    
    # Menu di selezione modello tra quelli effettivamente presenti nell'account
    selected_model = st.selectbox(
        "Modello Gemini attivo:",
        options=available_models,
        index=0
    )
    
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
        with st.spinner("Estrazione dati integrali dal CRM ed elaborazione..."):
            context_data = {}
            query_lower = user_query.lower()

            is_broad_query = any(k in query_lower for k in [
                "quante", "quali", "elenco", "tutte", "in corso", "attive", "target", "mandati", "lista", "aziende"
            ])

            if is_broad_query:
                context_data["aziende"] = get_entities("Company", top=50)
                context_data["opportunita"] = get_entities("Opportunity", top=50)
                context_data["target_leads"] = get_target_leads()

            words = [w for w in user_query.replace("?", "").replace(",", "").replace("=", " ").split() if len(w) > 3 and w.lower() not in [
                "quante", "quali", "abbiamo", "questo", "momento", "corso", "aziende", "cliente", "lead"
            ]]
            
            for w in words[:2]:
                search_res = search_crm_entities(w)
                if search_res["companies"] or search_res["leads"]:
                    context_data[f"ricerca_{w}"] = search_res
                    for c in search_res.get("companies", [])[:2]:
                        c_id = c.get("Id") or c.get("id")
                        if c_id:
                            context_data[f"attivita_company_{c_id}"] = get_activities("Company", c_id)
                    for l in search_res.get("leads", [])[:2]:
                        l_id = l.get("Id") or l.get("id")
                        if l_id:
                            context_data[f"attivita_lead_{l_id}"] = get_activities("Lead", l_id)

            answer_prompt = f"""
            Sei un assistente specializzato in operazioni M&A e advisory aziendale.
            Hai accesso ai dati reali estratti in tempo reale da 'CRM in Cloud'.
            
            Logica operativa:
            - Le aziende entrano come Lead sia per incarichi buy/sell generali sia per fare matching.
            - La fase 'target buy sell' identifica le aziende target proposte per un match.
            - Diventano 'Azienda' (Company) quando c'è un mandato formale o un NDA firmato.
            - L'oggetto 'Opportunità' unisce il cliente mandante con l'azienda target dopo l'NDA.
            - Nelle attività (Activities) sono registrati verbali di call, note ed email archiviate.
            - Analizza accuratamente ogni attributo di stato e i campi personalizzati (CustomFields), inclusi indicatori come 'in corso = sì' o simili.

            Dati estratti dal CRM (tutti i valori presenti sono preservati):
            {json.dumps(context_data, ensure_ascii=False, indent=2)}

            Domanda dell'utente:
            {user_query}

            Fornisci una risposta analitica, ordinata e professionale. Se individui le aziende corrispondenti, elencale con i rispettivi dettagli identificativi. Se il campo cercato non compare o ha un nome tecnico specifico, descrivi cosa emerge dai dati estratti.
            """

            try:
                answer, model_used = call_gemini_smart(answer_prompt, selected_model)
                st.markdown(answer)
                st.caption(f"⚡ Risposta elaborata con il modello: `{model_used}`")
                st.session_state.messages.append({"role": "assistant", "content": answer})
            except Exception as e:
                st.error(f"Tutti i tentativi sono al momento congestionati su Google ({e}). Riprova tra 10-15 secondi.")

            with st.expander("🔍 Dati integrali estratti dal CRM"):
                st.json(context_data)
