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

# --- Pulizia HTML e Testo Email ---
def clean_html_text(text: str) -> str:
    if not isinstance(text, str):
        return text
    # Rimuove blocchi CSS e script
    text = re.sub(r'<(style|script)[^>]*>.*?</\1>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    # Rimuove tag HTML preservando il testo
    text = re.sub(r'<[^>]+>', ' ', text)
    # Normalizza gli spazi multipli
    return re.sub(r'\s+', ' ', text).strip()

def clean_record(obj):
    """Rimuove chiavi vuote e pulisce il corpo delle email da tag HTML."""
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

# --- Paginazione e Caricamento Snapshot CRM (Inclusione Archivio Email) ---
@st.cache_data(ttl=1800, show_spinner=False)
def get_crm_snapshot():
    snapshot = {
        "timestamp": datetime.now(ZoneInfo("Europe/Rome")).strftime("%H:%M:%S"),
        "aziende": [],
        "lead": [],
        "opportunita": [],
        "attivita_recenti": [],
        "email_recenti": []
    }
    
    # 1. Aziende
    try:
        r = requests.get(f"{BASE_URL}/Company/Search?$top=120&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["aziende"] = clean_record(r.json())
    except Exception:
        pass

    # 2. Lead
    try:
        r = requests.get(f"{BASE_URL}/Lead/Search?$top=150&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["lead"] = clean_record(r.json())
    except Exception:
        pass

    # 3. Opportunità
    try:
        r = requests.get(f"{BASE_URL}/Opportunity/Search?$top=100&$orderby=Id desc", headers=crm_headers, timeout=12)
        if r.status_code == 200:
            snapshot["opportunita"] = clean_record(r.json())
    except Exception:
        pass

    # 4. Attività (fino a 300 record)
    all_activities = []
    for page in range(3):
        skip = page * 100
        try:
            r = requests.get(f"{BASE_URL}/Activity/Search?$top=100&$skip={skip}&$orderby=Id desc", headers=crm_headers, timeout=10)
            if r.status_code == 200:
                batch = r.json()
                if not batch:
                    break
                all_activities.extend(batch)
                if len(batch) < 100:
                    break
            else:
                break
        except Exception:
            break
    snapshot["attivita_recenti"] = clean_record(all_activities)

    # 5. Archivio Email dedicato (fino a 300 record)
    all_emails = []
    for page in range(3):
        skip = page * 100
        try:
            r = requests.get(f"{BASE_URL}/Email/Search?$top=100&$skip={skip}&$orderby=Id desc", headers=crm_headers, timeout=10)
            if r.status_code == 200:
                batch = r.json()
                if not batch:
                    break
                all_emails.extend(batch)
                if len(batch) < 100:
                    break
            else:
                break
        except Exception:
            break
    snapshot["email_recenti"] = clean_record(all_emails)

    return snapshot

with st.spinner("Sincronizzazione CRM (Aziende, Lead, Attività ed Email)..."):
    crm_data = get_crm_snapshot()

# --- Deep Fetch per Entità Specifica (Attività + Email collegate) ---
def fetch_entity_data(entity_type: str, entity_id: int):
    results = {"activities": [], "emails": []}
    # Attività
    try:
        r_act = requests.get(f"{BASE_URL}/{entity_type}/{entity_id}/Activities", headers=crm_headers, timeout=8)
        if r_act.status_code == 200:
            results["activities"] = clean_record(r_act.json())
    except Exception:
        pass
    
    # Email dedicate collegate
    try:
        r_em = requests.get(f"{BASE_URL}/{entity_type}/{entity_id}/Emails", headers=crm_headers, timeout=8)
        if r_em.status_code == 200:
            results["emails"] = clean_record(r_em.json())
    except Exception:
        pass

    return results

# --- Stopwords ed Estrazione Entità ---
EXCLUDED_WORDS = {
    "a", "ad", "al", "allo", "alla", "ai", "agli", "alle", "all",
    "da", "dal", "dallo", "dalla", "dai", "dagli", "dalle", "dall",
    "di", "del", "dello", "della", "dei", "degli", "delle", "dell",
    "in", "nel", "nello", "nella", "nei", "negli", "nelle", "nell",
    "su", "sul", "sullo", "sulla", "sui", "sugli", "sulle", "sull",
    "con", "col", "coi", "per", "tra", "fra",
    "il", "lo", "la", "i", "gli", "le", "l", "un", "uno", "una",
    "che", "chi", "cui", "quale", "quali", "quanto", "quanti", "quanta", "quante",
    "e", "ed", "o", "od", "se", "ma", "anche", "pure", "non", "piu", "più",
    "cosa", "come", "dove", "quando", "perche", "perché",
    "mi", "ti", "ci", "vi", "si", "me", "te", "noi", "voi", "loro",
    "ho", "hai", "ha", "abbiamo", "avete", "hanno",
    "sono", "sei", "era", "erano", "stato", "stata", "stati", "state", "siamo", "siete",
    "conversazione", "conversazioni", "colloquio", "colloqui", "call", "chiamata", "chiamate",
    "riunione", "riunioni", "meeting", "incontro", "incontri", "email", "mail", "messaggio", "messaggi",
    "parlato", "detto", "scritto", "discusso", "fatto", "sentito", "sentiti", "visto", "visti",
    "accordo", "accordi", "trattativa", "trattative", "aggiornamento", "aggiornamenti",
    "novita", "novità", "situazione", "stato", "sviluppi", "note", "nota", "verbale", "verbali",
    "dettaglio", "dettagli", "informazione", "informazioni", "contatto", "contatti",
    "dici", "dimmi", "trova", "cerca", "mostra", "elenco", "lista", "elencami", "spiega", "spiegami",
    "ultimo", "ultima", "ultimi", "ultime", "recenti", "recente", "questo", "questa", "questi", "queste"
}

def extract_proper_entities(query: str):
    tokens = re.findall(r"\b[a-zA-Z0-9_]{3,}\b", query.lower())
    return [t for t in tokens if t not in EXCLUDED_WORDS]

def score_and_filter(items: list, keywords: list, max_items: int = 15):
    scored = []
    for item in items:
        text = json.dumps(item, ensure_ascii=False).lower()
        matched_kws = [kw for kw in keywords if re.search(r'\b' + re.escape(kw) + r'\b', text)]
        if matched_kws:
            score = len(matched_kws)
            key_text = ""
            for k in ["companyName", "Name", "Subject", "subject", "Company", "Customer", "from", "to", "sender"]:
                if k in item and item[k]:
                    key_text += " " + str(item[k]).lower()
            if any(re.search(r'\b' + re.escape(kw) + r'\b', key_text) for kw in keywords):
                score += 3
            scored.append((score, item))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for score, item in scored[:max_items]]

def build_smart_context(query: str, data: dict):
    keywords = extract_proper_entities(query)
    query_lower = query.lower()
    
    is_broad = any(k in query_lower for k in [
        "quante", "quali", "elenco", "tutte", "in corso", "attive", "target", "mandati", "panoramica", "lista", "tutti"
    ])
    
    context = {}

    if keywords:
        matched_companies = score_and_filter(data.get("aziende", []), keywords, max_items=4)
        matched_leads = score_and_filter(data.get("lead", []), keywords, max_items=4)
        matched_opps = score_and_filter(data.get("opportunita", []), keywords, max_items=4)
        matched_activities = score_and_filter(data.get("attivita_recenti", []), keywords, max_items=12)
        matched_emails = score_and_filter(data.get("email_recenti", []), keywords, max_items=12)

        # Deep Fetch: recupera sia attività che archivio email dedicato per le aziende identificate
        for c in matched_companies:
            c_id = c.get("Id") or c.get("id")
            c_name = c.get("companyName") or c.get("Name") or f"Company_{c_id}"
            if c_id:
                direct = fetch_entity_data("Company", c_id)
                if direct["activities"]:
                    context[f"storico_attivita_{c_name}"] = direct["activities"]
                if direct["emails"]:
                    context[f"archivio_email_{c_name}"] = direct["emails"]

        for l in matched_leads:
            l_id = l.get("Id") or l.get("id")
            l_name = l.get("Company") or l.get("Subject") or f"Lead_{l_id}"
            if l_id:
                direct = fetch_entity_data("Lead", l_id)
                if direct["activities"]:
                    context[f"storico_attivita_{l_name}"] = direct["activities"]
                if direct["emails"]:
                    context[f"archivio_email_{l_name}"] = direct["emails"]

        if matched_companies or matched_leads or matched_opps or matched_activities or matched_emails or context:
            context["aziende_identificate"] = matched_companies
            context["lead_identificati"] = matched_leads
            context["opportunita_identificate"] = matched_opps
            context["attivita_recenti_pertinenti"] = matched_activities
            context["email_recenti_pertinenti"] = matched_emails
            return context

    if is_broad or not keywords:
        context["tutte_le_aziende"] = data.get("aziende", [])
        context["tutti_i_lead"] = data.get("lead", [])
        context["opportunita"] = data.get("opportunita", [])
        light_acts = []
        for a in data.get("attivita_recenti", [])[:25]:
            light_acts.append({
                "Id": a.get("Id"),
                "Oggetto": a.get("Subject") or a.get("Description"),
                "Data": a.get("Date") or a.get("CreatedDate"),
                "Azienda": a.get("companyName") or a.get("Company"),
                "Tipo": a.get("Type")
            })
        context["sintesi_attivita"] = light_acts
        
        light_emails = []
        for em in data.get("email_recenti", [])[:25]:
            light_emails.append({
                "Id": em.get("Id"),
                "Oggetto": em.get("Subject"),
                "Data": em.get("Date") or em.get("SentDate"),
                "Mittente": em.get("From") or em.get("Sender"),
                "Destinatario": em.get("To")
            })
        context["sintesi_email"] = light_emails
        return context

    return {"info": "Nessuna corrispondenza trovata nel CRM per i soggetti indicati."}

# --- Generatore Streaming ---
def stream_gemini_response(prompt_text: str, model_choice: str):
    response_stream = gemini_client.models.generate_content_stream(
        model=model_choice,
        contents=prompt_text
    )
    for chunk in response_stream:
        if chunk.text:
            yield chunk.text

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
    st.markdown(f"- Email archiviate: **{len(crm_data.get('email_recenti', []))}**")
    
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

user_query = st.chat_input("Chiedimi qualsiasi cosa sul CRM o su clienti, target ed email...")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        relevant_context = build_smart_context(user_query, crm_data)

        prompt = f"""
Sei un partner operativo esperto in operazioni M&A e advisory aziendale.
Hai accesso a tutti i dati rilevanti estratti in tempo reale da 'CRM in Cloud', compresi l'archivio attività e l'archivio dedicato delle email scambiate con i clienti:

DATI ESTRATTI DAL CRM (ATTIVITÀ, EMAIL E SCHEDE):
{json.dumps(relevant_context, ensure_ascii=False, indent=2)}

REGOLE DI INTERAZIONE:
1. CONSULTAZIONE COMPLETA: Esamina sia le note delle call/incontri (Activities) sia i contenuti delle corrispondenze email (Emails).
2. PRECISIONE OPERATIVA: Cita sempre date, interlocutori (mittenti/destinatari), argomenti centrali discussi ed eventuali decisioni o accordi.
3. CHIAREZZA E TRASPARENZA: Se un dettaglio non emerge chiaramente dai testi delle mail o delle note, segnalalo esplicitamente e fai una domanda mirata all'utente per chiarire il contesto.

DOMANDA DELL'UTENTE:
{user_query}
"""
        try:
            full_response = st.write_stream(stream_gemini_response(prompt, selected_model))
            st.session_state.messages.append({"role": "assistant", "content": full_response})
        except Exception as e:
            st.error(f"Errore temporaneo durante la generazione: {e}. Attendi qualche secondo e riprova.")

        with st.expander("🔍 Dati pertinenti estratti (Attività ed Email)"):
            st.json(relevant_context)
