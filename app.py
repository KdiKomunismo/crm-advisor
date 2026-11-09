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

def is_email_activity(act: dict) -> bool:
    """Identifica se un'attività di CRM in Cloud è una comunicazione email."""
    type_str = str(act.get("Type") or act.get("ActivityType") or act.get("TypeName") or "").lower()
    subject_str = str(act.get("Subject") or "").lower()
    if any(x in type_str for x in ["email", "mail", "posta"]):
        return True
    if any(subject_str.startswith(x) for x in ["re:", "fwd:", "r:", "i:"]):
        return True
    if act.get("From") or act.get("Sender") or act.get("To") or act.get("Direction"):
        return True
    return False

# --- Paginazione Profonda e Caricamento Snapshot CRM ---
@st.cache_data(ttl=1800, show_spinner=False)
def get_crm_snapshot():
    snapshot = {
        "timestamp": datetime.now(ZoneInfo("Europe/Rome")).strftime("%H:%M:%S"),
        "aziende": [],
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

    # 2. Lead (fino a 200)
    try:
        r = requests.get(f"{BASE_URL}/Lead/Search?$top=200&$orderby=Id desc", headers=crm_headers, timeout=12)
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

    # 4. Scansione profonda attività (fino a 800 record = 8 pagine)
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

    cleaned_acts = clean_record(all_raw_activities)
    
    # Separazione automatica tra Email e altre attività ordinarie
    regular_acts = []
    emails = []
    for a in cleaned_acts:
        if is_email_activity(a):
            emails.append(a)
        else:
            regular_acts.append(a)

    snapshot["attivita_recenti"] = regular_acts
    snapshot["email_archiviate"] = emails

    return snapshot

with st.spinner("Sincronizzazione CRM (Aziende, Storico Attività ed Email)..."):
    crm_data = get_crm_snapshot()

# --- Deep Fetch per Azienda o Lead ---
def fetch_entity_activities(entity_type: str, entity_id: int):
    try:
        r = requests.get(f"{BASE_URL}/{entity_type}/{entity_id}/Activities", headers=crm_headers, timeout=8)
        if r.status_code == 200:
            return clean_record(r.json())
    except Exception:
        pass
    return []

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
            for k in ["companyName", "Name", "Subject", "subject", "Company", "Customer", "from", "to"]:
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
    
    # Se compare "alberoni", includiamo come alias "outsideview"
    search_keywords = list(keywords)
    if "alberoni" in search_keywords and "outsideview" not in search_keywords:
        search_keywords.append("outsideview")
    
    is_broad = any(k in query_lower for k in [
        "quante", "quali", "elenco", "tutte", "in corso", "attive", "target", "mandati", "panoramica", "lista", "tutti"
    ])
    
    context = {}

    if search_keywords:
        matched_companies = score_and_filter(data.get("aziende", []), search_keywords, max_items=4)
        matched_leads = score_and_filter(data.get("lead", []), search_keywords, max_items=4)
        matched_opps = score_and_filter(data.get("opportunita", []), search_keywords, max_items=4)

        # 1. Recupero incontri e call ordinarie
        matched_activities = score_and_filter(data.get("attivita_recenti", []), search_keywords, max_items=15)

        # 2. Recupero Email: tutte le mail pertinenti che citano anche uno solo dei soggetti in merito all'operazione
        matched_emails = score_and_filter(data.get("email_archiviate", []), search_keywords, max_items=25)

        # 3. Deep Fetch specifico per le aziende individuate (recupero storico completo)
        deep_activities = []
        for c in matched_companies:
            c_id = c.get("Id") or c.get("id")
            if c_id:
                raw_direct = fetch_entity_activities("Company", c_id)
                deep_activities.extend(raw_direct)

        for l in matched_leads:
            l_id = l.get("Id") or l.get("id")
            if l_id:
                raw_direct = fetch_entity_activities("Lead", l_id)
                deep_activities.extend(raw_direct)

        for act in deep_activities:
            if is_email_activity(act):
                if act not in matched_emails:
                    matched_emails.append(act)
            else:
                if act not in matched_activities:
                    matched_activities.append(act)

        context["aziende_pertinenti"] = matched_companies
        context["lead_pertinenti"] = matched_leads
        context["opportunita_pertinenti"] = matched_opps
        context["incontri_call_e_note"] = matched_activities[:15]
        context["email_e_corrispondenze_deal"] = matched_emails[:25]
        return context

    if is_broad or not search_keywords:
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
        return context

    return {"info": "Nessuna corrispondenza trovata nel CRM per i soggetti indicati."}

# --- Streaming con Fallback Automatico (3.8 -> 3.5) ---
def stream_gemini_smart(prompt_text: str, user_choice: str):
    if user_choice == "gemini-3.5-flash":
        models_to_try = ["gemini-3.5-flash", "gemini-3.8-flash"]
    else:
        # Default / Auto: prova 3.8, se occupato scala all'istante su 3.5
        models_to_try = ["gemini-3.8-flash", "gemini-3.5-flash"]
    
    last_err = None
    for idx, model_name in enumerate(models_to_try):
        try:
            stream = gemini_client.models.generate_content_stream(
                model=model_name,
                contents=prompt_text
            )
            # Test del primo blocco per verificare la disponibilità del server
            first_chunk = None
            for chunk in stream:
                first_chunk = chunk
                break
            
            if first_chunk and first_chunk.text:
                yield first_chunk.text
            
            for chunk in stream:
                if chunk.text:
                    yield chunk.text
            return
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            if idx < len(models_to_try) - 1 and any(x in err_str for x in ["503", "429", "unavailable", "demand", "resource_exhausted", "quota"]):
                time.sleep(1)
                continue
            else:
                raise last_err

# --- Barra Laterale ---
st.title("💼 CRM in Cloud - M&A Advisor")

with st.sidebar:
    st.header("⚙️ Configurazione")
    
    selected_model = st.selectbox(
        "Modello Gemini:",
        options=["Auto (3.8 Flash con fallback 3.5)", "gemini-3.8-flash", "gemini-3.5-flash"],
        index=0
    )
    
    st.markdown("---")
    st.markdown("**Stato Archivio CRM:**")
    st.markdown(f"- Ultimo sync: `{crm_data.get('timestamp')}`")
    st.markdown(f"- Aziende: **{len(crm_data.get('aziende', []))}**")
    st.markdown(f"- Lead: **{len(crm_data.get('lead', []))}**")
    st.markdown(f"- Opportunità: **{len(crm_data.get('opportunita', []))}**")
    st.markdown(f"- Incontri / Call / Note: **{len(crm_data.get('attivita_recenti', []))}**")
    st.markdown(f"- Email archiviate: **{len(crm_data.get('email_archiviate', []))}**")
    
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

user_query = st.chat_input("Chiedimi qualsiasi cosa su clienti, target, call ed email...")

if user_query:
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        relevant_context = build_smart_context(user_query, crm_data)

        prompt = f"""
Sei un partner operativo esperto in operazioni M&A e advisory aziendale.
Lavori a fianco dell'utente e hai accesso all'archivio integrale di 'CRM in Cloud'.

DATI ESTRATTI DAL CRM:
{json.dumps(relevant_context, ensure_ascii=False, indent=2)}

REGOLE DI INTERAZIONE:
1. INTEGRAZIONE DELLE EMAIL (FONDAMENTALE):
   Esamina la sezione 'email_e_corrispondenze_deal'. Considera tutte le comunicazioni email nelle quali, anche interloquendo con uno solo dei soggetti (es. solo con Tomware o solo con Paolo Alberoni / Outsideview), si è parlato del deal, dell'operazione, di accordi, valutazioni o prossimi passi.
2. DISTINZIONE DELLE FONTI:
   Distingui chiaramente tra quanto emerso nelle riunioni/videocall (sezione incontri) e quanto scambiato tramite email (sezione corrispondenze), citando date, mittenti, destinatari e oggetti.
3. RIGORE ED ESAUSTIVITÀ:
   Riporta in modo ordinato la cronologia dei fatti e dei temi discussi. Se dalle email emergono documenti allegati o questioni aperte, evidenziali con precisione.

DOMANDA DELL'UTENTE:
{user_query}
"""
        try:
            full_response = st.write_stream(stream_gemini_smart(prompt, selected_model))
            st.session_state.messages.append({"role": "assistant", "content": full_response})
        except Exception as e:
            st.error(f"Errore temporaneo nei servizi Google: {e}. Attendi qualche secondo e riprova.")

        with st.expander("🔍 Dati pertinenti estratti (Attività ed Email)"):
            st.json(relevant_context)
