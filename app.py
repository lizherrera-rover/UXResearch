"""
=======================================================
  ROVER RESEARCH — Interview Scheduler
  Streamlit app for scheduling UX research interviews
=======================================================
"""

import os
os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

import streamlit as st
import pandas as pd
import json
import base64
import hashlib
import math
import random
import secrets
import tempfile
from datetime import datetime, timedelta, date
from pathlib import Path

# Persist PKCE verifier across OAuth redirect (session_state is lost during redirect)
PKCE_FILE   = Path(tempfile.gettempdir()) / "rover_uxr_pkce.txt"
CONFIG_FILE = Path(__file__).parent / ".uxr_config.json"

def load_config():
    """Load persisted config (API keys, etc.) from disk."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}

def save_config(data: dict):
    """Merge data into persisted config and write to disk."""
    cfg = load_config()
    cfg.update(data)
    CONFIG_FILE.write_text(json.dumps(cfg))

# Google APIs
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import gspread


# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Research Interview Scheduler",
    page_icon="🐾",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Constants ──────────────────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

REDIRECT_URI = st.secrets.get("google_oauth", {}).get("redirect_uri", "http://localhost:8501")
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"

BASE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1QlMWrOLbHIXII4NkZCEA3tbA7DI9neJ-PzkcbbkyNmA/edit?usp=sharing"

ROVER_PURPLE = "#8B5CF6"

# ── Session state init ─────────────────────────────────────────────────────────
def init_session_state():
    defaults = {
        "step": 1,
        "credentials": None,
        "user_email": None,
        "df_candidates": None,
        "df_sample": None,
        "calendar_link": None,
        "study_title": "",
        "study_description": "",
        "interview_duration": 45,
        "emails_generated": [],
        "oauth_state": None,
        "code_verifier": None,
        "email_template": "",
        "incentive_amount": 50,
        "incentive_currency": "USD",
        "incentive_currencies": ["USD"],
        "incentive_amounts": {"USD": 50},
        "incentive_type": "Sitter incentive",
        "is_base_sheet": False,
        "_user_type_sel": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

init_session_state()


# ── Styling ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

    :root {
        --rover-green: #00BF6F;
        --rover-green-dark: #008A4E;
        --rover-green-soft: #EAF8F0;
        --ink: #111827;
        --muted: #667085;
        --line: #E6E8EC;
        --panel: #FFFFFF;
        --panel-soft: #F8FAF9;
        --warning-bg: #FFF8ED;
        --warning-line: #F4D5A6;
        --warning-ink: #8A4B08;
        --success-bg: #F0FBF5;
        --success-line: #BFEBD1;
        --success-ink: #065F46;
        --shadow-tiny: 0 2px 10px rgba(17, 24, 39, 0.04);
    }

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }

    .stApp {
        background: #FFFFFF;
        color: var(--ink);
    }

    section.main > div.block-container {
        padding-top: 2rem;
        padding-bottom: 4rem;
        max-width: 1220px;
    }

    [data-testid="stSidebar"] {
        background: #FFFFFF;
        border-right: 1px solid var(--line);
    }

    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: .5rem;
    }

    .sidebar-brand {
        background: #FFFFFF;
        border: 1px solid #CFEFDC;
        border-radius: 18px;
        padding: 1rem;
        box-shadow: var(--shadow-tiny);
        margin-bottom: .5rem;
    }

    .sidebar-brand img {
        background: #FFFFFF;
        padding: 0;
        border-radius: 8px;
    }

    .sidebar-title {
        font-size: 1rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        color: var(--ink);
        margin-top: .65rem;
    }

    .sidebar-subtitle {
        font-size: .78rem;
        line-height: 1.35;
        color: var(--muted);
        margin-top: .15rem;
    }

    .current-step-card {
        background: var(--rover-green-soft);
        color: var(--ink);
        border: 1px solid #CFEFDC;
        border-radius: 16px;
        padding: .8rem .9rem;
        margin: .75rem 0 .55rem;
    }

    .current-step-eyebrow {
        color: var(--rover-green-dark);
        font-size: .66rem;
        text-transform: uppercase;
        letter-spacing: .08em;
        font-weight: 800;
        margin-bottom: .18rem;
    }

    .current-step-title {
        color: var(--ink);
        font-size: .9rem;
        font-weight: 750;
        line-height: 1.25;
    }

    .step-header {
        font-size: 1.9rem;
        font-weight: 800;
        color: var(--ink);
        margin-bottom: 0.25rem;
        letter-spacing: -0.045em;
        line-height: 1.1;
    }

    .step-sub {
        font-size: .98rem;
        color: var(--muted);
        margin-bottom: 1.35rem;
        max-width: 780px;
        line-height: 1.52;
    }

    .stat-box {
        background: #FFFFFF;
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 1.05rem 1rem;
        text-align: left;
        box-shadow: var(--shadow-tiny);
        position: relative;
        overflow: hidden;
    }

    .stat-box:before {
        content: "";
        position: absolute;
        inset: 0 0 auto 0;
        height: 3px;
        background: var(--rover-green);
    }

    .stat-num {
        font-size: 1.85rem;
        font-weight: 800;
        color: var(--ink);
        line-height: 1.05;
        letter-spacing: -0.035em;
    }

    .stat-label {
        font-size: 0.72rem;
        color: var(--muted);
        margin-top: 5px;
        text-transform: uppercase;
        letter-spacing: .07em;
        font-weight: 700;
    }

    .section-label {
        font-size: 0.7rem;
        font-weight: 800;
        color: var(--rover-green-dark);
        text-transform: uppercase;
        letter-spacing: .08em;
        margin-bottom: 0.48rem;
    }

    .tz-card, .success-banner {
        background: var(--success-bg);
        border: 1px solid var(--success-line);
        border-radius: 14px;
        padding: 0.75rem .9rem;
        margin-bottom: 0.6rem;
        color: var(--success-ink);
        font-size: 0.88rem;
        line-height: 1.45;
    }

    .tz-warn {
        background: var(--warning-bg);
        border: 1px solid var(--warning-line);
        border-radius: 14px;
        padding: 0.75rem .9rem;
        margin-bottom: 0.6rem;
        color: var(--warning-ink);
        font-size: 0.88rem;
        line-height: 1.45;
    }

    .windows-list {
        border-top: 1px solid var(--line);
        border-bottom: 1px solid var(--line);
        margin: .35rem 0 1rem;
    }

    .window-row {
        display: grid;
        grid-template-columns: minmax(160px, 1.4fr) minmax(130px, .8fr) minmax(180px, 1fr);
        gap: 1rem;
        align-items: center;
        padding: .72rem 0;
        border-bottom: 1px solid #F0F1F3;
        font-size: .9rem;
    }

    .window-row:last-child { border-bottom: 0; }

    .window-country { font-weight: 750; color: var(--ink); }
    .window-meta { color: var(--muted); font-size: .82rem; }
    .window-chip {
        display: inline-flex;
        width: fit-content;
        align-items: center;
        border: 1px solid #CFEFDC;
        background: var(--rover-green-soft);
        color: var(--rover-green-dark);
        border-radius: 999px;
        padding: .28rem .62rem;
        font-weight: 750;
        font-size: .82rem;
    }

    .minimal-panel {
        background: #FFFFFF;
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: .85rem 1rem;
        margin-bottom: .85rem;
    }

    div[data-testid="stExpander"] {
        border: 1px solid var(--line) !important;
        border-radius: 16px !important;
        background: #FFFFFF !important;
        box-shadow: none;
        overflow: hidden;
    }

    div[data-testid="stExpander"] summary {
        font-weight: 750 !important;
        color: var(--ink) !important;
    }

    div[data-testid="stTabs"] button {
        font-weight: 700;
        color: var(--muted);
    }

    div[data-testid="stTabs"] button[aria-selected="true"] {
        color: var(--rover-green-dark);
    }

    div[data-testid="stDataFrame"] {
        border-radius: 16px;
        overflow: hidden;
        border: 1px solid var(--line);
        box-shadow: none;
    }

    .stTextInput input,
    .stNumberInput input,
    .stTextArea textarea,
    div[data-baseweb="select"] > div,
    div[data-baseweb="base-input"] {
        border-radius: 12px !important;
        border-color: #D0D5DD !important;
        background-color: #FFFFFF !important;
    }

    .stTextArea textarea { line-height: 1.5 !important; }

    .stButton > button,
    .stDownloadButton > button,
    .stLinkButton > a {
        border-radius: 12px !important;
        font-weight: 750 !important;
        border: 1px solid #D0D5DD !important;
        box-shadow: none;
        transition: all .12s ease-in-out;
    }

    .stButton > button:hover,
    .stDownloadButton > button:hover,
    .stLinkButton > a:hover {
        transform: translateY(-1px);
        border-color: var(--rover-green) !important;
    }

    .stButton > button[kind="primary"],
    .stDownloadButton > button[kind="primary"] {
        background: var(--rover-green) !important;
        border-color: var(--rover-green) !important;
        color: #FFFFFF !important;
    }

    .stButton > button[kind="primary"]:hover,
    .stDownloadButton > button[kind="primary"]:hover {
        background: var(--rover-green-dark) !important;
        border-color: var(--rover-green-dark) !important;
    }

    .stAlert { border-radius: 14px; }
    code { border-radius: 10px !important; }

    hr {
        border: none;
        height: 1px;
        background: var(--line);
        margin: 1.1rem 0;
    }

    @media (max-width: 760px) {
        section.main > div.block-container { padding-left: 1rem; padding-right: 1rem; }
        .step-header { font-size: 1.55rem; }
        .step-sub { font-size: .92rem; }
        .stat-num { font-size: 1.55rem; }
        .window-row { grid-template-columns: 1fr; gap: .35rem; }
    }
</style>
""", unsafe_allow_html=True)

# ── OAuth helpers ──────────────────────────────────────────────────────────────
def _get_client_config():
    """Load OAuth client config from Streamlit Secrets or local credentials.json."""
    if "google_oauth" in st.secrets:
        s = st.secrets["google_oauth"]
        return {
            "web": {
                "client_id": s["client_id"],
                "client_secret": s["client_secret"],
                "auth_uri": s.get("auth_uri", "https://accounts.google.com/o/oauth2/auth"),
                "token_uri": s.get("token_uri", "https://oauth2.googleapis.com/token"),
                "redirect_uris": [REDIRECT_URI],
            }
        }
    # Fallback: local file (dev only — never commit this file)
    return json.loads(CREDENTIALS_FILE.read_text())

def get_flow():
    return Flow.from_client_config(
        _get_client_config(),
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )

def _pkce_pair():
    """Generate a PKCE code_verifier + code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest   = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge

def get_auth_url():
    flow = get_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    st.session_state["oauth_state"] = state
    # google-auth-oauthlib >= 1.2.0 auto-generates PKCE; capture the verifier
    # so the callback tab (new session) can use it during token exchange.
    code_verifier = getattr(flow, "code_verifier", None)
    if code_verifier is None:
        # Fallback: check the inner OAuth2 client
        try:
            code_verifier = flow.oauth2session._client.code_verifier
        except Exception:
            pass
    if code_verifier:
        verifier_file = Path(tempfile.gettempdir()) / f"uxr_pkce_{state}.txt"
        verifier_file.write_text(code_verifier)
    return auth_url

def exchange_code_for_credentials(code):
    flow = get_flow()
    # Retrieve PKCE verifier stored during get_auth_url(), indexed by OAuth state.
    # The callback arrives in a new browser tab (new Streamlit session), so
    # session_state from the original tab is unavailable — we use a temp file instead.
    state = st.query_params.get("state", "")
    code_verifier = None
    try:
        if state:
            verifier_file = Path(tempfile.gettempdir()) / f"uxr_pkce_{state}.txt"
            if verifier_file.exists():
                code_verifier = verifier_file.read_text().strip()
                verifier_file.unlink(missing_ok=True)
    except Exception:
        pass

    if code_verifier:
        flow.fetch_token(code=code, code_verifier=code_verifier)
    else:
        flow.fetch_token(code=code)
    return flow.credentials

def get_user_email(creds):
    try:
        service = build("oauth2", "v2", credentials=creds)
        info = service.userinfo().get().execute()
        return info.get("email")
    except Exception:
        return "unknown@rover.com"

def creds_to_dict(creds):
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else [],
    }

def dict_to_creds(d):
    return Credentials(
        token=d["token"],
        refresh_token=d.get("refresh_token"),
        token_uri=d["token_uri"],
        client_id=d["client_id"],
        client_secret=d["client_secret"],
        scopes=d.get("scopes"),
    )

# ── Sidebar navigation ─────────────────────────────────────────────────────────
def sidebar():
    with st.sidebar:
        steps = [
            (1, "Sign in with Google", "Connect Calendar, Gmail, and Sheets."),
            (2, "Select candidates", "Load the pool, filter, and sample."),
            (3, "Design invitation", "Write the invite and merge fields."),
            (4, "Set up calendar", "Create the booking page and save the link."),
            (5, "Send invites", "Review, send, and log invite dates."),
        ]

        current = st.session_state["step"]
        current_label = next((label for n, label, _ in steps if n == current), "Interview Scheduler")

        st.markdown(
            """
            <div class="sidebar-brand">
                <img src="https://upload.wikimedia.org/wikipedia/commons/d/d3/Rover.com_logo.jpg" style="width:112px; display:block;" />
                <div class="sidebar-title">Research Scheduler</div>
                <div class="sidebar-subtitle">Recruit, schedule, and invite participants.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="current-step-card">
                <div class="current-step-eyebrow">Current step</div>
                <div class="current-step-title">Step {current} · {current_label}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        max_reached = current
        for n, label, desc in steps:
            if n < current:
                icon = "✓"
                prefix = "Done"
            elif n == current:
                icon = "●"
                prefix = "Now"
            else:
                icon = "○"
                prefix = "Locked"

            if n <= max_reached:
                button_label = f"{icon} {prefix}: {label}"
                if st.button(button_label, key=f"nav_{n}", use_container_width=True):
                    st.session_state["step"] = n
                    st.rerun()
                st.caption(desc)
            else:
                st.markdown(
                    f"<div style='color:#9CA3AF;font-size:.88rem;padding:.35rem .15rem'>{icon} {prefix}: {label}</div>"
                    f"<div style='color:#A1A1AA;font-size:.75rem;margin:-.25rem 0 .45rem .15rem'>{desc}</div>",
                    unsafe_allow_html=True,
                )

        st.markdown("---")

        if st.session_state["user_email"]:
            st.markdown(
                f"""
                <div style="background:#FFFFFF;border:1px solid #E5E7EB;border-radius:14px;padding:.75rem .85rem;margin-bottom:.65rem">
                    <div style="font-size:.68rem;text-transform:uppercase;letter-spacing:.08em;color:#008A4E;font-weight:800;margin-bottom:.2rem">Signed in</div>
                    <div style="font-size:.82rem;color:#111827;line-height:1.3;word-break:break-word">{st.session_state['user_email']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        if st.session_state["step"] > 1:
            if st.button("↩ Start over", use_container_width=True):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

# ── Step footer nav ───────────────────────────────────────────────────────────
def render_step_footer():
    current = st.session_state["step"]
    steps = [
        (1, "Sign in"),
        (2, "Select candidates"),
        (3, "Design invitation"),
        (4, "Set up calendar"),
        (5, "Send invites"),
    ]
    st.markdown("<div style='height:2.25rem'></div>", unsafe_allow_html=True)
    pills = ""
    for n, label in steps:
        if n < current:
            bg, fg, dot = "#059669", "#fff", "✓"
            border = "#059669"
        elif n == current:
            bg, fg, dot = "#00BF6F", "#fff", str(n)
            border = "#00BF6F"
        else:
            bg, fg, dot = "#F3F4F6", "#9CA3AF", str(n)
            border = "#E5E7EB"
        txt_color = "#008A4E" if n == current else ("#047857" if n < current else "#9CA3AF")
        weight = "800" if n == current else "650"
        pills += (
            f'<div style="display:flex;align-items:center;gap:8px;min-width:0">'
            f'<span style="width:26px;height:26px;border-radius:999px;background:{bg};color:{fg};border:1px solid {border};'
            f'display:flex;align-items:center;justify-content:center;font-size:0.72rem;font-weight:800;flex-shrink:0">{dot}</span>'
            f'<span style="font-size:0.76rem;color:{txt_color};font-weight:{weight};white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{label}</span>'
            f'</div>'
        )
        if n < len(steps):
            line = "#A7F3D0" if n < current else "#E5E7EB"
            pills += f'<div style="flex:1;height:1px;background:{line};min-width:12px;margin:0 8px"></div>'
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0;padding:1rem 1.05rem;'
        f'background:rgba(255,255,255,.86);border:1px solid #E5E7EB;border-radius:18px;margin-top:0.5rem;'
        f'box-shadow:0 2px 10px rgba(17,24,39,.05);overflow-x:auto">'
        f'{pills}</div>',
        unsafe_allow_html=True,
    )


# ── Scheduling window helper ──────────────────────────────────────────────────
def calculate_scheduling_windows(sample, researcher_tz: str, duration_minutes: int = 45):
    """Return realistic candidate scheduling windows in the researcher timezone.

    Uses country-level working hours and conservative timezone coverage when a
    country has multiple common timezones. This keeps the UX useful without
    pretending we know each candidate's exact city. Humanity survives another
    approximation.
    """
    import pytz

    COUNTRY_TZ = {
        "US": (["America/New_York", "America/Chicago", "America/Los_Angeles"], "United States"),
        "CA": (["America/Toronto", "America/Vancouver"], "Canada"),
        "GB": (["Europe/London"], "United Kingdom"),
        "DE": (["Europe/Berlin"], "Germany"),
        "FR": (["Europe/Paris"], "France"),
        "NL": (["Europe/Amsterdam"], "Netherlands"),
        "AU": (["Australia/Sydney"], "Australia"),
        "NZ": (["Pacific/Auckland"], "New Zealand"),
        "NO": (["Europe/Oslo"], "Norway"),
        "SE": (["Europe/Stockholm"], "Sweden"),
        "ES": (["Europe/Madrid"], "Spain"),
        "MX": (["America/Mexico_City"], "Mexico"),
        "BR": (["America/Sao_Paulo"], "Brazil"),
        "IT": (["Europe/Rome"], "Italy"),
    }
    EU_COUNTRIES = {"GB", "DE", "FR", "NL", "NO", "SE", "ES", "IT", "BE", "AT", "CH", "PT", "DK", "FI"}

    def working_hours(country_code: str):
        return (10, 19) if country_code in EU_COUNTRIES else (9, 18)

    def researcher_working_hours(tz_name: str):
        return (10, 19) if tz_name.startswith("Europe") else (9, 18)

    def to_hhmm(total_minutes: int):
        return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"

    def format_slots(slots):
        if not slots:
            return "No clean overlap"
        slots = sorted(slots)
        groups = []
        current = [slots[0]]
        for s in slots[1:]:
            if s - current[-1] == 30:
                current.append(s)
            else:
                groups.append(current)
                current = [s]
        groups.append(current)

        labels = []
        for g in groups:
            if len(g) == 1:
                labels.append(to_hhmm(g[0]))
            else:
                labels.append(f"{to_hhmm(g[0])}–{to_hhmm(g[-1])}")
        return ", ".join(labels)

    if sample is None or "country_code" not in sample.columns or len(sample) == 0:
        return [], [], "No candidates selected"

    rtz = pytz.timezone(researcher_tz)
    base_day = datetime.now(rtz).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=7)
    r_start, r_end = researcher_working_hours(researcher_tz)
    candidate_duration = timedelta(minutes=int(duration_minutes or 45))
    country_counts = sample["country_code"].dropna().astype(str).str.upper().value_counts()
    total = int(len(sample))
    all_researcher_slots = {m for m in range(r_start * 60, r_end * 60, 30) if m + duration_minutes <= r_end * 60}

    rows = []
    global_slots = set(all_researcher_slots)

    for country, count in country_counts.items():
        if country not in COUNTRY_TZ:
            rows.append({
                "country": country,
                "name": country,
                "count": int(count),
                "pct": int(round(int(count) / total * 100)),
                "window": "Unknown timezone",
                "slots": set(),
                "status": "warn",
                "note": "Country not mapped",
            })
            global_slots = set()
            continue

        tz_list, country_name = COUNTRY_TZ[country]
        c_start, c_end = working_hours(country)
        valid_slots = set(all_researcher_slots)

        for ctz_name in tz_list:
            ctz = pytz.timezone(ctz_name)
            tz_valid = set()
            for minute in all_researcher_slots:
                start_local = base_day + timedelta(minutes=minute)
                end_local = start_local + candidate_duration
                candidate_start = start_local.astimezone(ctz)
                candidate_end = end_local.astimezone(ctz)
                if (
                    c_start <= candidate_start.hour + candidate_start.minute / 60
                    and candidate_end.hour + candidate_end.minute / 60 <= c_end
                    and candidate_start.date() == candidate_end.date()
                ):
                    tz_valid.add(minute)
            valid_slots &= tz_valid

        global_slots &= valid_slots
        rows.append({
            "country": country,
            "name": country_name,
            "count": int(count),
            "pct": int(round(int(count) / total * 100)),
            "window": format_slots(valid_slots),
            "slots": valid_slots,
            "status": "ok" if valid_slots else "warn",
            "note": "Conservative across major time zones" if len(tz_list) > 1 else "Working-hours overlap",
        })

    global_label = format_slots(global_slots) if rows else "No candidates selected"
    return rows, sorted(global_slots), global_label

# ── STEP 1 — Google sign-in ────────────────────────────────────────────────────
def step_sign_in():
    st.markdown('<div class="step-header">👋 Sign in with Google</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-sub">We need access to your Google Calendar and Gmail to schedule interviews.</div>', unsafe_allow_html=True)

    # Handle OAuth callback
    params = st.query_params
    if "code" in params and st.session_state["credentials"] is None:
        with st.spinner("Authenticating..."):
            try:
                creds = exchange_code_for_credentials(params["code"])
                st.session_state["credentials"] = creds_to_dict(creds)
                st.session_state["user_email"] = get_user_email(creds)
                st.session_state["step"] = 2
                st.query_params.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Authentication failed: {e}")
        return

    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        st.markdown("####")
        auth_url = get_auth_url()
        st.link_button(
            "🔐  Sign in with Google",
            auth_url,
            use_container_width=True,
        )
        st.caption("You'll be asked to grant access to Google Calendar, Gmail, and Sheets.")
    render_step_footer()

# ── STEP 2 — Load & select candidates ─────────────────────────────────────────
def step_load_candidates():

    CURRENCY_SYMBOLS = {
        "USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$",
        "AUD": "A$", "MXN": "MX$", "BRL": "R$", "NOK": "kr", "SEK": "kr", "NZD": "NZ$",
    }
    COUNTRY_CURRENCY = {
        "US": "USD", "CA": "CAD", "GB": "GBP",
        "DE": "EUR", "FR": "EUR", "ES": "EUR", "IT": "EUR", "NL": "EUR",
        "BE": "EUR", "AT": "EUR", "PT": "EUR", "FI": "EUR", "IE": "EUR",
        "NO": "NOK", "SE": "SEK", "AU": "AUD", "NZ": "NZD", "MX": "MXN", "BR": "BRL",
    }

    def stat_box(value, label):
        display = f"{value:,}" if isinstance(value, int) else str(value)
        return (
            f'<div class="stat-box"><div class="stat-num">{display}</div>'
            f'<div class="stat-label">{label}</div></div>'
        )

    def section_hdr(n, title, subtitle=""):
        sub = f'<div style="font-size:0.82rem;color:#6b7280;margin-top:1px">{subtitle}</div>' if subtitle else ""
        return (
            f'<div style="display:flex;align-items:baseline;gap:10px;margin:1.4rem 0 0.8rem">'
            f'<span style="background:#00BF6F;color:#fff;border-radius:50%;min-width:24px;height:24px;'
            f'display:flex;align-items:center;justify-content:center;font-size:0.7rem;font-weight:700">{n}</span>'
            f'<div><span style="font-size:1rem;font-weight:700;color:#111827">{title}</span>{sub}</div></div>'
        )

    def is_recent_incentive(val, months=6):
        if pd.isna(val) or str(val).strip() in ("", "None", "nan", "0"):
            return False
        try:
            dt = pd.to_datetime(val, dayfirst=False)
            cutoff = datetime.now() - timedelta(days=months * 30)
            return dt.replace(tzinfo=None) >= cutoff
        except Exception:
            return False

    st.markdown('<div class="step-header">Select your candidates</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-sub">Follow the steps below to load your pool, set filters, configure the incentive, and pick your sample.</div>', unsafe_allow_html=True)

    # ── Load data ──────────────────────────────────────────────────────────────
    if st.session_state["df_candidates"] is None:
        st.markdown(
            '<div style="background:#FFFFFF;border:1px solid #CFEFDC;border-radius:12px;'
            'padding:1rem 1.2rem;margin-bottom:1.5rem">'
            '<div style="font-weight:700;margin-bottom:4px">📊 Load your candidate pool</div>'
            '<div style="font-size:0.88rem;color:#6b7280">Use the Rover Research base (recommended) or bring your own data. '
            'The base automatically excludes people with a UX incentive in the last 6 months or more than 5 accumulated incentives.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        tab_base, tab_sheet, tab_csv = st.tabs(["📊 Rover base (recommended)", "🔗 Other Google Sheet", "📁 Upload CSV"])
        with tab_base:
            st.caption("Official Research Ops spreadsheet — segments, UX history, and services for every user.")
            if st.button("Load Rover candidate base", type="primary", key="load_base"):
                with st.spinner("Loading..."):
                    try:
                        creds = dict_to_creds(st.session_state["credentials"])
                        gc = gspread.authorize(creds)
                        sh = gc.open_by_url(BASE_SHEET_URL)
                        df = pd.DataFrame(sh.get_worksheet(0).get_all_records())
                        st.session_state["df_candidates"] = df
                        st.session_state["is_base_sheet"] = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not read sheet: {e}")
        with tab_sheet:
            sheet_url = st.text_input("Google Sheet URL", placeholder="https://docs.google.com/spreadsheets/d/...", label_visibility="collapsed")
            if st.button("Load sheet", key="load_sheet") and sheet_url:
                with st.spinner("Loading..."):
                    try:
                        creds = dict_to_creds(st.session_state["credentials"])
                        gc = gspread.authorize(creds)
                        sh = gc.open_by_url(sheet_url)
                        df = pd.DataFrame(sh.get_worksheet(0).get_all_records())
                        st.session_state["df_candidates"] = df
                        st.session_state["is_base_sheet"] = False
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not read sheet: {e}")
        with tab_csv:
            uploaded = st.file_uploader("Upload CSV", type=["csv"], label_visibility="collapsed")
            if uploaded:
                st.session_state["df_candidates"] = pd.read_csv(uploaded)
                st.session_state["is_base_sheet"] = False
                st.rerun()
        return

    df = st.session_state["df_candidates"].copy()
    is_base = st.session_state.get("is_base_sheet", False)

    # ── Auto-exclusions ────────────────────────────────────────────────────────
    if is_base:
        excl_recent, excl_many = 0, 0
        if "most_recent_ux_incentive" in df.columns:
            mask = df["most_recent_ux_incentive"].apply(is_recent_incentive)
            excl_recent = int(mask.sum())
            df = df[~mask]
        if "total_ux_incentives" in df.columns:
            mask = pd.to_numeric(df["total_ux_incentives"], errors="coerce").fillna(0) > 5
            excl_many = int(mask.sum())
            df = df[~mask]
        if excl_recent or excl_many:
            parts = []
            if excl_recent:
                parts.append(f"<strong>{excl_recent}</strong> with a UX incentive in the last 6 months")
            if excl_many:
                parts.append(f"<strong>{excl_many}</strong> with 5+ accumulated UX incentives")
            st.markdown(
                f'<div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;'
                f'padding:0.7rem 1rem;margin-bottom:0.75rem;color:#9a3412;font-size:0.87rem">'
                f'🚫 Automatically excluded: {" and ".join(parts)}.</div>',
                unsafe_allow_html=True,
            )

    # ── 1 · Who are you recruiting? ───────────────────────────────────────────
    st.markdown(section_hdr("1", "Who are you recruiting?", "Choose the user type. Filters will adapt to your selection."), unsafe_allow_html=True)

    col_o, col_s, col_desc = st.columns([1, 1, 2])
    with col_o:
        owner_active = st.session_state.get("_user_type_sel") == "Owners"
        if st.button("🏠  Owners", use_container_width=True,
                     type="primary" if owner_active else "secondary", key="btn_owners"):
            st.session_state["_user_type_sel"] = "Owners"
            st.session_state["df_sample"] = None
            st.rerun()
    with col_s:
        sitter_active = st.session_state.get("_user_type_sel") == "Sitters"
        if st.button("🐾  Sitters", use_container_width=True,
                     type="primary" if sitter_active else "secondary", key="btn_sitters"):
            st.session_state["_user_type_sel"] = "Sitters"
            st.session_state["df_sample"] = None
            st.rerun()
    with col_desc:
        if owner_active:
            st.markdown('<div class="tz-card" style="margin:0">Pet parents who book services on Rover — boarding, walking, day care, and more.</div>', unsafe_allow_html=True)
        elif sitter_active:
            st.markdown('<div class="tz-card" style="margin:0">Service providers who care for pets — they offer boarding, day care, walking, or drop-in visits.</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:#9ca3af;font-size:0.88rem;padding-top:0.6rem">← Select who you\'re recruiting to continue.</div>', unsafe_allow_html=True)

    user_type_sel = st.session_state.get("_user_type_sel")
    if not user_type_sel:
        render_step_footer()
        return

    # Filter to selected user type
    country_col = next((c for c in ["country", "country_code"] if c in df.columns), None)
    lang_col    = next((c for c in ["user_language", "language", "locale"] if c in df.columns), None)
    ut_key = user_type_sel[:-1].lower()  # "Owners" → "owner", "Sitters" → "sitter"
    df_type = df[df["user_type"].str.lower() == ut_key].copy() if "user_type" in df.columns else df.copy()

    # Pool stats
    st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
    sc1, sc2, sc3, sc4, sc5 = st.columns(5)
    with sc1:
        st.markdown(stat_box(len(df_type), user_type_sel), unsafe_allow_html=True)
    with sc2:
        st.markdown(stat_box(int(df_type[country_col].nunique()) if country_col else "—", "Countries"), unsafe_allow_html=True)
    with sc3:
        st.markdown(stat_box(int(df_type[lang_col].nunique()) if lang_col else "—", "Languages"), unsafe_allow_html=True)
    with sc4:
        st.markdown(stat_box(int(df_type["grouped_segment"].nunique()) if "grouped_segment" in df_type.columns else "—", "Segments"), unsafe_allow_html=True)
    with sc5:
        if st.button("↺ Reset", use_container_width=True):
            st.session_state["df_candidates"] = None
            st.session_state["df_sample"] = None
            st.session_state["is_base_sheet"] = False
            st.session_state["_user_type_sel"] = None
            st.rerun()

    # ── 2 · Refine your pool ───────────────────────────────────────────────────
    st.markdown(section_hdr("2", "Refine your pool", "All filters are optional — your eligible pool updates as you select."), unsafe_allow_html=True)

    with st.expander("Filters", expanded=True):
        f_col1, f_col2 = st.columns(2)

        with f_col1:
            country_sel = []
            if country_col:
                st.markdown("**Country**")
                st.caption("Filter by market. Useful when your study targets a specific region.")
                country_sel = st.multiselect("Country", sorted(df_type[country_col].dropna().unique().tolist()), label_visibility="collapsed")

            lang_sel = []
            if lang_col:
                st.markdown("**Language**")
                st.caption("The user's preferred language. Important if the interview won't be in English.")
                lang_sel = st.multiselect("Language", sorted(df_type[lang_col].dropna().unique().tolist()), label_visibility="collapsed")

            segment_sel = []
            if "grouped_segment" in df_type.columns:
                st.markdown("**Activity segment**")
                st.caption("**Core** = frequent users · **Infrequent** = occasional · **Churn** = inactive · **New** = recently joined.")
                segment_sel = st.multiselect("Segment", sorted(df_type["grouped_segment"].dropna().unique().tolist()), label_visibility="collapsed")

            booking_min_val, booking_max_val = None, None
            if "total_bookings_l12" in df_type.columns:
                nums = pd.to_numeric(df_type["total_bookings_l12"], errors="coerce")
                pool_bmin, pool_bmax = int(nums.min() or 0), int(nums.max() or 0)
                st.markdown("**Bookings in last 12 months**")
                st.caption("Number of completed bookings in the past year. Leave at defaults to include all.")
                bc1, bc2 = st.columns(2)
                with bc1:
                    booking_min_val = st.number_input("Min", min_value=pool_bmin, max_value=pool_bmax, value=pool_bmin, step=1)
                with bc2:
                    booking_max_val = st.number_input("Max", min_value=pool_bmin, max_value=pool_bmax, value=pool_bmax, step=1)

        with f_col2:
            provider_stage_sel = []
            boarding_sel = daycare_sel = walking_sel = "All"

            if user_type_sel == "Sitters":
                st.markdown(
                    '<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;'
                    'padding:0.5rem 0.8rem;margin-bottom:0.65rem;font-weight:600;color:#166534;font-size:0.83rem">'
                    '🐾 Sitter-specific filters</div>', unsafe_allow_html=True,
                )
                if "provider_stage" in df_type.columns:
                    STAGE_DESCS = {
                        "new": "Recently joined, few or no bookings yet",
                        "emerging": "Early traction, starting to get regular bookings",
                        "established": "Consistent bookings, proven track record",
                        "top": "High-volume, highly-rated sitters",
                        "lapsed": "Was active before, no recent bookings",
                        "inactive": "Registered but no meaningful activity",
                    }
                    all_stages = sorted(df_type["provider_stage"].dropna().unique().tolist())
                    stage_opts = [
                        f"{s}  —  {STAGE_DESCS[s.lower()]}" if s.lower() in STAGE_DESCS else s
                        for s in all_stages
                    ]
                    stage_map = dict(zip(stage_opts, all_stages))
                    st.markdown("**Provider stage**")
                    st.caption("Where the sitter is in their Rover lifecycle.")
                    sel_display = st.multiselect("Provider stage", stage_opts, label_visibility="collapsed")
                    provider_stage_sel = [stage_map[s] for s in sel_display]

                st.markdown("**Services offered**")
                st.caption("Filter by which services the sitter has active on their profile.")
                if "offers_boarding" in df_type.columns:
                    boarding_sel = st.selectbox("🏠 Boarding — hosts pets overnight", ["All", "Offers it", "Doesn't offer it"])
                if "offers_day_care" in df_type.columns:
                    daycare_sel = st.selectbox("☀️ Day Care — cares for pets during the day", ["All", "Offers it", "Doesn't offer it"])
                if "offers_dog_walking" in df_type.columns:
                    walking_sel = st.selectbox("🦮 Dog Walking", ["All", "Offers it", "Doesn't offer it"])
            else:
                st.markdown(
                    '<div style="background:#F8FAF9;border:1px solid #DDE5DF;border-radius:8px;'
                    'padding:0.5rem 0.8rem;margin-bottom:0.65rem;font-weight:600;color:#2F6B4F;font-size:0.83rem">'
                    '🏠 Owner filters</div>', unsafe_allow_html=True,
                )
                st.caption("Use the filters on the left to refine your owner pool by country, language, segment, and bookings.")

        # Apply filters
        filtered = df_type.copy()
        if country_sel and country_col:
            filtered = filtered[filtered[country_col].isin(country_sel)]
        if lang_sel and lang_col:
            filtered = filtered[filtered[lang_col].isin(lang_sel)]
        if segment_sel and "grouped_segment" in filtered.columns:
            filtered = filtered[filtered["grouped_segment"].isin(segment_sel)]
        if booking_min_val is not None and booking_max_val is not None and "total_bookings_l12" in filtered.columns:
            nums = pd.to_numeric(filtered["total_bookings_l12"], errors="coerce")
            filtered = filtered[(nums >= booking_min_val) & (nums <= booking_max_val)]
        if provider_stage_sel and "provider_stage" in filtered.columns:
            filtered = filtered[filtered["provider_stage"].isin(provider_stage_sel)]
        if boarding_sel != "All" and "offers_boarding" in filtered.columns:
            filtered = filtered[pd.to_numeric(filtered["offers_boarding"], errors="coerce") == (1 if boarding_sel == "Offers it" else 0)]
        if daycare_sel != "All" and "offers_day_care" in filtered.columns:
            filtered = filtered[pd.to_numeric(filtered["offers_day_care"], errors="coerce") == (1 if daycare_sel == "Offers it" else 0)]
        if walking_sel != "All" and "offers_dog_walking" in filtered.columns:
            filtered = filtered[pd.to_numeric(filtered["offers_dog_walking"], errors="coerce") == (1 if walking_sel == "Offers it" else 0)]

        if len(filtered) == 0:
            st.warning("No candidates match these filters. Try broadening your selection.")
            return

        st.markdown(
            f'<div style="background:#F0FBF5;border:1px solid #BFEBD1;border-radius:8px;'
            f'padding:0.6rem 1rem;margin-top:0.5rem;font-size:0.88rem;color:#065F46">'
            f'✅ <strong>{len(filtered):,} eligible {user_type_sel.lower()}</strong> match your filters</div>',
            unsafe_allow_html=True,
        )

    # Warnings
    if "grouped_segment" in filtered.columns:
        risky = filtered[filtered["grouped_segment"].str.lower().isin(["infrequent", "churn"])]
        if len(risky) > 0:
            pct = int(round(len(risky) / len(filtered) * 100))
            st.markdown(
                f'<div class="tz-warn" style="margin-top:0.5rem">⚠️ <strong>{len(risky)} {user_type_sel.lower()} ({pct}%)</strong> '
                f'have segment <strong>Infrequent or Churn</strong> — low or no recent activity. '
                f'They may have lower response rates.</div>', unsafe_allow_html=True,
            )
    if user_type_sel == "Owners" and "total_bookings_l12" in filtered.columns:
        inactive = filtered[pd.to_numeric(filtered["total_bookings_l12"], errors="coerce").fillna(-1) == 0]
        if len(inactive) > 0:
            pct = int(round(len(inactive) / len(filtered) * 100))
            st.markdown(
                f'<div class="tz-warn" style="margin-top:0.5rem">⚠️ <strong>{len(inactive)} owners ({pct}%)</strong> '
                f'had no bookings in the last 12 months — they may not be using Rover actively.</div>',
                unsafe_allow_html=True,
            )

    # ── 3 · Set the incentive ──────────────────────────────────────────────────
    st.markdown(section_hdr("3", "Set the incentive", "Default adapts to the selected segment. Add no incentive, raffle, or gift card when needed."), unsafe_allow_html=True)

    # Auto-detect currencies
    auto_currencies = []
    if country_col:
        seen = set()
        for cc in filtered[country_col].dropna().unique():
            cur = COUNTRY_CURRENCY.get(str(cc).upper(), "USD")
            if cur not in seen:
                auto_currencies.append(cur)
                seen.add(cur)
    if not auto_currencies:
        auto_currencies = ["USD"]

    if not st.session_state.get("incentive_currencies"):
        st.session_state["incentive_currencies"] = auto_currencies

    default_type = "Sitter incentive" if user_type_sel == "Sitters" else "Owner credits"
    if st.session_state.get("_last_incentive_user_type") != user_type_sel:
        st.session_state["incentive_type"] = default_type
        st.session_state["_last_incentive_user_type"] = user_type_sel

    incentive_options = [default_type, "No incentive", "Raffle", "Amazon Gift card"]
    current_incentive = st.session_state.get("incentive_type", default_type)
    if current_incentive not in incentive_options:
        current_incentive = default_type

    inc_col1, inc_col2 = st.columns([1, 1])
    with inc_col1:
        incentive_type = st.selectbox(
            "Incentive type",
            incentive_options,
            index=incentive_options.index(current_incentive),
            help="Default adapts to the selected segment. Use raffle or gift card when the incentive is not paid through Rover.",
        )
        st.session_state["incentive_type"] = incentive_type

    needs_amount = incentive_type != "No incentive"
    with inc_col2:
        if needs_amount:
            selected_currencies = st.multiselect(
                "Currency",
                list(CURRENCY_SYMBOLS.keys()),
                default=st.session_state.get("incentive_currencies", auto_currencies),
                help="Auto-detected from your pool's countries. You can add or remove currencies.",
            )
            st.session_state["incentive_currencies"] = selected_currencies
        else:
            selected_currencies = []
            st.info("No incentive will be shown in the invite copy.")

    if needs_amount and selected_currencies:
        incentive_amounts = st.session_state.get("incentive_amounts", {})
        amt_cols = st.columns(min(len(selected_currencies), 4))
        for i, currency in enumerate(selected_currencies):
            with amt_cols[i % min(len(selected_currencies), 4)]:
                sym = CURRENCY_SYMBOLS.get(currency, currency)
                incentive_amounts[currency] = st.number_input(
                    f"Amount ({sym} {currency})",
                    min_value=0,
                    value=int(incentive_amounts.get(currency, 50)),
                    step=5,
                )
        st.session_state["incentive_amounts"] = incentive_amounts
        st.session_state["incentive_currency"] = selected_currencies[0]
        st.session_state["incentive_amount"] = incentive_amounts.get(selected_currencies[0], 50)

    # ── 4 · How many do you need? ──────────────────────────────────────────────
    st.markdown(section_hdr("4", "How many do you need?", "Set your target number of completed interviews and the session duration."), unsafe_allow_html=True)

    col_n, col_dur, col_btn = st.columns([1, 1, 2])
    with col_n:
        n_interviews = st.number_input(
            "Target completions",
            min_value=1,
            max_value=min(len(filtered), 500),
            value=min(15, len(filtered)),
            step=1,
        )
    with col_dur:
        dur = st.selectbox(
            "Session duration",
            [30, 45, 60], index=1,
            format_func=lambda x: f"{x} min",
        )
        st.session_state["interview_duration"] = dur
    with col_btn:
        st.markdown("<div style='height:1.85rem'></div>", unsafe_allow_html=True)
        if st.button(f"Select {n_interviews} candidates randomly", type="primary", use_container_width=True):
            sample = filtered.sample(n=min(n_interviews, len(filtered)), random_state=None)
            st.session_state["df_sample"] = sample.reset_index(drop=True)
            st.rerun()

    # Recruitment tip
    invite_n  = math.ceil(n_interviews * 1.35)
    noshows   = math.ceil(invite_n * 0.15)
    recommend = invite_n + noshows
    st.markdown(
        f'<div style="background:#F8FAF9;border:1px solid #DDE5DF;border-radius:10px;'
        f'padding:0.75rem 1rem;margin-top:0.5rem;font-size:0.87rem;color:#344054">'
        f'💡 <strong>Recruitment tip:</strong> To complete <strong>{n_interviews}</strong> interviews, '
        f'invite at least <strong>{recommend} people</strong> — '
        f'{invite_n} to cover non-responses (+35%), plus {noshows} extra for no-shows (~15%).</div>',
        unsafe_allow_html=True,
    )

    # ── Selected candidates table ──────────────────────────────────────────────
    if st.session_state["df_sample"] is not None:
        sample = st.session_state["df_sample"]
        st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
        st.markdown(f'<div class="section-label">Selected candidates — {len(sample)}</div>', unsafe_allow_html=True)
        preferred_order = [
            "first_name", "email", "user_type", "country_code", "user_language",
            "grouped_segment", "segment", "provider_stage",
            "total_bookings_l12", "total_ux_incentives",
            "offers_boarding", "offers_day_care", "offers_dog_walking",
            "most_recent_ux_incentive", "person_id",
        ]
        cols_to_show = [c for c in preferred_order if c in sample.columns]
        cols_to_show += [c for c in sample.columns if c not in cols_to_show]
        st.dataframe(sample[cols_to_show], use_container_width=True, hide_index=True)
        st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
        if st.button("Continue →", type="primary"):
            st.session_state["step"] = 3
            st.rerun()
    render_step_footer()


# ── STEP 3 — Design invitation ─────────────────────────────────────────────────
def step_sample_design():
    import pytz

    st.markdown('<div class="step-header">Design your invitation</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-sub">Set the email subject, write your template, and review realistic scheduling windows for your candidates.</div>', unsafe_allow_html=True)

    df_candidates = st.session_state.get("df_candidates")
    spreadsheet_cols = list(df_candidates.columns) if df_candidates is not None else []
    duration = st.session_state.get("interview_duration", 45)

    TITLE_OPTIONS = [
        "Meet with Rover",
        "Chat with Rover 🐾",
        "Share your Rover experience",
        "Help us improve Rover",
        "We'd love to hear from you",
        "A conversation with the Rover team",
        "Rover wants to hear from you",
        "Help shape the future of Rover",
        "Rover Research: A friendly chat",
        "30 minutes to help improve Rover",
        "Custom...",
    ]

    # ── Title ──────────────────────────────────────────────────────────────────
    st.markdown('<div class="section-label">Email subject</div>', unsafe_allow_html=True)
    current_title = st.session_state.get("study_title") or ""
    default_idx = TITLE_OPTIONS.index(current_title) if current_title in TITLE_OPTIONS else len(TITLE_OPTIONS) - 1
    selected_title = st.selectbox("Email subject", TITLE_OPTIONS, index=default_idx, label_visibility="collapsed")
    if selected_title == "Custom...":
        custom_title = st.text_input(
            "Custom email subject",
            value=current_title if current_title not in TITLE_OPTIONS else "",
            placeholder="e.g. Share your Rover experience",
            label_visibility="collapsed",
        )
        st.session_state["study_title"] = custom_title
    else:
        st.session_state["study_title"] = selected_title

    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # ── Email template ─────────────────────────────────────────────────────────
    st.markdown('<div class="section-label">Invite email template</div>', unsafe_allow_html=True)

    # Variable picker dropdown
    SPECIAL_VARS = {
        "[first_name]": "Candidate's first name",
        "[user_type]": "owner / sitter",
        "[duration]": f"Interview duration ({duration} min)",
        "[BOOKING_LINK]": "Calendar booking link (from Step 4)",
        "[incentive_type]": "Selected incentive type (set in Step 2)",
        "[incentive_summary]": "Ready-to-send incentive sentence (set in Step 2)",
    }
    all_vars = list(SPECIAL_VARS.keys()) + [f"[{c}]" for c in spreadsheet_cols if f"[{c}]" not in SPECIAL_VARS]

    with st.expander("📎 Variable picker — click to see available merge fields"):
        col_pick, col_copy = st.columns([1, 2])
        with col_pick:
            picked = st.selectbox("Variable", all_vars, label_visibility="collapsed")
        with col_copy:
            hint = SPECIAL_VARS.get(picked, f"Spreadsheet column: {picked[1:-1]}")
            st.markdown(f"<div style='padding-top:0.5rem;color:#6b7280;font-size:0.85rem'>{hint}</div>", unsafe_allow_html=True)
        st.code(picked, language=None)
        st.caption("Copy the variable above and paste it anywhere in your template.")

    DEFAULT_TEMPLATE = """Hi [first_name],

We're exploring new ideas to improve the experience at Rover, and we'd love to hear your perspective.

We're inviting [user_type]s in [country_code] to take part in a [duration]-minute remote interview.

Here's what to expect:

👉 Click [BOOKING_LINK] to access the scheduling page and choose a time that works for you.

The session will take place over a video call.

As a thank-you, [incentive_summary].

If the available times don't work for you, or if the session is already fully booked, don't worry — we'll keep you in mind for future research opportunities.

Thanks for helping us make Rover better!

Best,
The Rover Research Team"""

    email_template = st.text_area(
        "Template",
        value=st.session_state["email_template"] or DEFAULT_TEMPLATE,
        height=300,
        key="email_template_input",
        label_visibility="collapsed",
    )
    st.session_state["email_template"] = email_template
    st.caption("Variables in [brackets] are replaced per person in Step 5.")

    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # ── Researcher timezone ────────────────────────────────────────────────────
    st.markdown('<div class="section-label">Your timezone</div>', unsafe_allow_html=True)

    TZ_LABELS = {
        "Europe/Madrid": "🇪🇸 Barcelona / Madrid",
        "America/Los_Angeles": "🇺🇸 Seattle / Los Angeles (PT)",
        "America/New_York": "🇺🇸 New York (ET)",
        "America/Chicago": "🇺🇸 Chicago (CT)",
        "America/Denver": "🇺🇸 Denver (MT)",
        "America/Toronto": "🇨🇦 Toronto (ET)",
        "America/Vancouver": "🇨🇦 Vancouver (PT)",
        "Europe/London": "🇬🇧 London",
        "Europe/Berlin": "🇩🇪 Berlin",
        "Europe/Paris": "🇫🇷 Paris",
        "Europe/Amsterdam": "🇳🇱 Amsterdam",
        "Europe/Rome": "🇮🇹 Rome",
        "America/Sao_Paulo": "🇧🇷 São Paulo",
        "America/Mexico_City": "🇲🇽 Mexico City",
        "Australia/Sydney": "🇦🇺 Sydney",
        "Pacific/Auckland": "🇳🇿 Auckland",
    }

    researcher_tz = st.selectbox(
        "Timezone", list(TZ_LABELS.keys()), index=0,
        format_func=lambda tz: TZ_LABELS.get(tz, tz),
        label_visibility="collapsed",
    )
    st.session_state["researcher_tz"] = researcher_tz

    # ── Timezone overlap cards ─────────────────────────────────────────────────
    sample = st.session_state.get("df_sample")
    if sample is not None and "country_code" in sample.columns:
        st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
        rows, global_slots, global_label = calculate_scheduling_windows(sample, researcher_tz, duration)
        total = len(sample)

        st.markdown(f'<div class="section-label">Scheduling windows · {total} candidates selected</div>', unsafe_allow_html=True)
        html_rows = ""
        for row in rows:
            chip_color = "#EAF8F0" if row["status"] == "ok" else "#FFF8ED"
            chip_border = "#CFEFDC" if row["status"] == "ok" else "#F4D5A6"
            chip_text = "#008A4E" if row["status"] == "ok" else "#8A4B08"
            html_rows += (
                f'<div class="window-row">'
                f'<div><div class="window-country">{row["name"]}</div>'
                f'<div class="window-meta">{row["count"]} candidate{"s" if row["count"] != 1 else ""} · {row["pct"]}%</div></div>'
                f'<div class="window-meta">{row["note"]}</div>'
                f'<div><span class="window-chip" style="background:{chip_color};border-color:{chip_border};color:{chip_text}">{row["window"]}</span></div>'
                f'</div>'
            )
        st.markdown(f'<div class="windows-list">{html_rows}</div>', unsafe_allow_html=True)
        if global_slots:
            st.markdown(
                f'<div class="success-banner">Best shared start times in your timezone: <strong>{global_label}</strong></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="tz-warn">No shared working-hours overlap across all selected candidates. Consider separate schedules by market.</div>',
                unsafe_allow_html=True,
            )

    # ── Continue ───────────────────────────────────────────────────────────────
    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
    if not st.session_state.get("study_title"):
        st.info("Add an email subject to continue.")
    else:
        if st.button("Continue → Set up Calendar", type="primary"):
            st.session_state["step"] = 4
            st.rerun()
    render_step_footer()


# ── STEP 4 — Calendar setup ────────────────────────────────────────────────────
def step_calendar_setup():
    st.markdown('<div class="step-header">Set up your calendar</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-sub">Use the content below to create your appointment schedule in Google Calendar, then paste your booking link.</div>', unsafe_allow_html=True)

    study_title   = st.session_state.get("study_title", "")
    duration      = st.session_state.get("interview_duration", 45)
    researcher_tz = st.session_state.get("researcher_tz", "America/New_York")
    sample        = st.session_state.get("df_sample")

    # ── Candidate timezone context ─────────────────────────────────────────────
    if sample is not None and "country_code" in sample.columns:
        rows, global_slots, global_label = calculate_scheduling_windows(sample, researcher_tz, duration)
        st.markdown('<div class="section-label">Scheduling context</div>', unsafe_allow_html=True)
        if rows:
            compact_rows = ""
            for row in rows:
                compact_rows += (
                    f'<div class="window-row">'
                    f'<div><div class="window-country">{row["name"]}</div>'
                    f'<div class="window-meta">{row["count"]} candidate{"s" if row["count"] != 1 else ""}</div></div>'
                    f'<div class="window-meta">Suggested start times</div>'
                    f'<div><span class="window-chip">{row["window"]}</span></div>'
                    f'</div>'
                )
            st.markdown(f'<div class="windows-list">{compact_rows}</div>', unsafe_allow_html=True)
            if global_slots:
                st.caption(f"Best shared start times in your timezone: {global_label}")
            else:
                st.caption("No single working-hours window covers everyone. Create separate availability blocks by market.")

    # ── Copy-paste content ─────────────────────────────────────────────────────
    col_l, col_r = st.columns([1, 1.35])

    with col_l:
        st.markdown('<div class="section-label">Appointment title</div>', unsafe_allow_html=True)
        st.text_input(
            "Appointment title",
            value=study_title or "Your email subject here",
            label_visibility="collapsed",
            key="appointment_title_display",
        )

    with col_r:
        st.markdown('<div class="section-label">Description template</div>', unsafe_allow_html=True)
        DESCRIPTION_TEMPLATES = {
            "Short": (
                "Pick a day and time that works best for you.\n\n"
                "This calendar detects your time zone automatically. Please double-check before confirming."
            ),
            "Full invite": (
                "Hi from Rover! 🐾\n\n"
                "Thanks for signing up to speak with us. This will be a remote conversation with the Rover Research Team via Google Meet. "
                "As a thank-you for your time, you’ll receive the incentive specified in your invitation after completing the session.\n\n"
                "Please join from a quiet place with audio and video enabled. No preparation is needed.\n\n"
                "Thanks again for helping us improve the Rover experience!\n\n"
                "Best,\n"
                "Rover Research Team"
            ),
        }
        selected_desc = st.radio("Template", list(DESCRIPTION_TEMPLATES.keys()), horizontal=True, label_visibility="collapsed")
        if st.session_state.get("_last_desc_template") != selected_desc:
            st.session_state["calendar_description"] = DESCRIPTION_TEMPLATES[selected_desc]
            st.session_state["_last_desc_template"] = selected_desc
        calendar_description = st.text_area(
            "Description",
            value=st.session_state.get("calendar_description", DESCRIPTION_TEMPLATES[selected_desc]),
            height=180,
            label_visibility="collapsed",
            key="cal_desc_editor",
        )
        st.session_state["calendar_description"] = calendar_description

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    # ── Instructions + open button ─────────────────────────────────────────────
    st.markdown('<div class="section-label">Steps in Google Calendar</div>', unsafe_allow_html=True)
    col_steps, col_btn = st.columns([4, 1])
    with col_steps:
        st.markdown(
            f"1. Open Google Calendar and create an **Appointment schedule**.  \n"
            f"2. Use the title and description above.  \n"
            f"3. Set duration to **{duration} min** and timezone to **{researcher_tz}**.  \n"
            f"4. Add availability using the scheduling context above.  \n"
            f"5. Save, copy the booking page link, and paste it below."
        )
    with col_btn:
        st.markdown("<div style='margin-top:0.35rem'></div>", unsafe_allow_html=True)
        st.link_button("Open Google Calendar →", "https://calendar.google.com/calendar/u/0/r/appointment", use_container_width=True)

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    # ── Booking link ───────────────────────────────────────────────────────────
    st.markdown('<div class="section-label">Booking link</div>', unsafe_allow_html=True)
    manual_link = st.text_input(
        "Booking link",
        value=st.session_state.get("calendar_link") or "",
        placeholder="https://calendar.google.com/calendar/appointments/schedules/...",
        label_visibility="collapsed",
    )

    col_save, col_test, col_cont, col_gap = st.columns([1, 1, 1, 3])
    with col_save:
        save_link = st.button("💾 Save link", use_container_width=True)
    with col_test:
        if st.session_state.get("calendar_link"):
            st.link_button("🔗 Test the link", st.session_state["calendar_link"], use_container_width=True)
    with col_cont:
        if st.session_state.get("calendar_link"):
            if st.button("Continue →", type="primary", use_container_width=True):
                st.session_state["step"] = 5
                st.rerun()

    if manual_link and (save_link or manual_link != st.session_state.get("calendar_link", "")):
        st.session_state["calendar_link"] = manual_link
        st.rerun()

    if st.session_state.get("calendar_link"):
        st.markdown(
            f'<div class="success-banner" style="margin-top:0.5rem">📎 <a href="{st.session_state["calendar_link"]}" target="_blank">'
            f'{st.session_state["calendar_link"]}</a></div>',
            unsafe_allow_html=True,
        )
    render_step_footer()


# ── Write-back: research invite date to base sheet ────────────────────────────
def write_invite_dates_to_sheet(creds, emails_sent: list[str]) -> tuple[int, str | None]:
    """
    For each email in emails_sent, find the row in the base sheet and write
    today's date to the 'research_invite_date' column (creates it if missing).
    Returns (n_updated, error_message_or_None).
    """
    try:
        gc = gspread.authorize(creds)
        sh = gc.open_by_url(BASE_SHEET_URL)
        ws = sh.get_worksheet(0)

        headers = ws.row_values(1)

        # Find or create the research_invite_date column
        col_name = "research_invite_date"
        if col_name in headers:
            date_col_idx = headers.index(col_name) + 1  # 1-based
        else:
            date_col_idx = len(headers) + 1
            ws.update_cell(1, date_col_idx, col_name)

        # Build email → row index mapping (2-based, row 1 is header)
        if "email" not in headers:
            return 0, "No se encontró la columna 'email' en la sheet."

        all_records = ws.get_all_records()
        email_to_row = {
            str(r.get("email", "")).strip().lower(): i + 2
            for i, r in enumerate(all_records)
        }

        today_str = date.today().isoformat()
        updated = 0
        for email in emails_sent:
            key = email.strip().lower()
            if key in email_to_row:
                ws.update_cell(email_to_row[key], date_col_idx, today_str)
                updated += 1

        return updated, None
    except Exception as e:
        return 0, str(e)


# ── STEP 5 — Generate & send emails ───────────────────────────────────────────
def step_generate_and_send():
    st.markdown('<div class="step-header">✉️ Generate & send invites</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-sub">Fill your email template with each candidate\'s data, review, and send.</div>', unsafe_allow_html=True)

    sample        = st.session_state["df_sample"]
    booking_link  = st.session_state.get("calendar_link", "")
    study_title   = st.session_state.get("study_title", "")
    study_description = st.session_state.get("study_description", "")
    duration      = st.session_state.get("interview_duration", 45)
    base_template = st.session_state.get("email_template", "")

    # ── Email settings ─────────────────────────────────────────────────────────
    st.markdown("#### Email settings")
    col_s, col_subj = st.columns([1, 2])
    with col_s:
        sender_email = st.text_input("Send from", value="research@rover.com")
    with col_subj:
        email_subject = st.text_input(
            "Subject line",
            value=study_title,
        )

    st.markdown("---")

    # ── Generate section ───────────────────────────────────────────────────────
    st.markdown("#### Prepare emails")

    if st.button("📋 Prepare emails from template", type="primary", use_container_width=False):
        gen_tmpl = True
    else:
        gen_tmpl = False

    if gen_tmpl:
        template = base_template or ""
        emails = []

        incentive_currency = st.session_state.get("incentive_currency", "$")
        incentive_amount   = st.session_state.get("incentive_amount", 50)

        for _, row in sample.iterrows():
            first_name = str(row.get("first_name") or "there")
            user_type  = str(row.get("user_type", "user"))

            body = template

            # Replace all spreadsheet columns: [column_name] → row value
            for col in sample.columns:
                val = row.get(col, "")
                body = body.replace(f"[{col}]", str(val) if pd.notna(val) else "")

            # Special variables
            body = body.replace("[first_name]", first_name)
            body = body.replace("[user_type]", user_type)
            body = body.replace("[BOOKING_LINK]", booking_link or "[booking link]")
            body = body.replace("[duration]", str(duration))
            body = body.replace("[incentive_currency]", incentive_currency)
            body = body.replace("[incentive_amount]", str(incentive_amount))
            # Per-candidate incentive phrasing
            row_user_type = str(row.get("user_type", "")).strip().lower()
            selected_incentive_type = st.session_state.get("incentive_type", "Sitter incentive")
            if selected_incentive_type == "No incentive":
                inc_type_phrase = "no incentive"
                inc_summary = "there is no incentive attached to this study"
            elif selected_incentive_type == "Raffle":
                inc_type_phrase = "raffle entry"
                inc_summary = "you'll be entered into a raffle after completing the session"
            elif selected_incentive_type == "Amazon Gift card":
                inc_type_phrase = "Amazon gift card"
                inc_summary = f"you'll receive a {incentive_amount} {incentive_currency} Amazon gift card after completing the session"
            elif row_user_type == "sitter":
                inc_type_phrase = "directly in your account"
                inc_summary = f"you'll receive {incentive_amount} {incentive_currency} directly in your Rover account"
            else:
                inc_type_phrase = "as Rover credits that you can use on upcoming services"
                inc_summary = f"you'll receive {incentive_amount} {incentive_currency} as Rover credits for upcoming services"
            body = body.replace("[incentive_type]", inc_type_phrase)
            body = body.replace("[incentive_summary]", inc_summary)

            emails.append({
                "email": str(row.get("email", "")),
                "first_name": first_name,
                "user_type": user_type,
                "body": body,
            })

        st.session_state["emails_generated"] = emails
        st.success(f"✅ {len(emails)} emails ready — all variables filled in!")

    # ── Review & edit ──────────────────────────────────────────────────────────
    if st.session_state["emails_generated"]:
        emails = st.session_state["emails_generated"]

        st.markdown("---")
        st.markdown(f"### Review emails ({len(emails)} total)")
        st.caption("You can edit any email before sending.")

        for idx, email_data in enumerate(emails):
            with st.expander(f"📧 {email_data['first_name']} — {email_data['email']}", expanded=(idx < 2)):
                edited_body = st.text_area(
                    "Email body",
                    value=email_data["body"],
                    key=f"email_body_{idx}",
                    height=200,
                )
                emails[idx]["body"] = edited_body

        st.session_state["emails_generated"] = emails

        st.markdown("---")
        st.markdown("### Send all emails")

        col1, col2 = st.columns([2, 1])
        with col1:
            st.info(f"Ready to send **{len(emails)} emails** from **{sender_email}**\n\nSubject: _{email_subject}_")
        with col2:
            if st.button("🚀 Send all invites", type="primary", use_container_width=True):
                creds = dict_to_creds(st.session_state["credentials"])
                gmail_service = build("gmail", "v1", credentials=creds)

                sent_count = 0
                errors = []
                send_progress = st.progress(0, text="Sending emails...")

                for idx, email_data in enumerate(emails):
                    try:
                        from email.mime.text import MIMEText
                        msg = MIMEText(email_data["body"])
                        msg["To"]      = email_data["email"]
                        msg["From"]    = sender_email
                        msg["Subject"] = email_subject
                        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
                        gmail_service.users().messages().send(
                            userId="me", body={"raw": raw},
                        ).execute()
                        sent_count += 1
                    except Exception as e:
                        errors.append(f"{email_data['email']}: {e}")
                    send_progress.progress((idx + 1) / len(emails), text=f"Sent {sent_count}/{len(emails)}...")

                if sent_count == len(emails):
                    st.markdown(f'<div class="success-banner">🎉 All <strong>{sent_count} invites sent</strong> successfully!</div>', unsafe_allow_html=True)
                else:
                    st.success(f"Sent {sent_count}/{len(emails)} emails.")
                    if errors:
                        st.error("Errors:\n" + "\n".join(errors))

                # Write invite dates back to base sheet
                if st.session_state.get("is_base_sheet") and sent_count > 0:
                    with st.spinner("Registrando fechas de invitación en la base de candidatos..."):
                        sent_emails = [e["email"] for e in emails[:sent_count]]
                        n_written, wb_error = write_invite_dates_to_sheet(creds, sent_emails)
                    if wb_error:
                        st.warning(f"⚠️ No se pudieron registrar las fechas en la sheet: {wb_error}")
                    else:
                        st.markdown(
                            f'<div class="success-banner" style="margin-top:0.4rem">'
                            f'📅 Fecha de invitación registrada para <strong>{n_written} personas</strong> '
                            f'en la columna <code>research_invite_date</code> de la base de candidatos.</div>',
                            unsafe_allow_html=True,
                        )

                st.markdown("---")
                st.markdown("#### 🏁 You're done!")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Invites sent", sent_count)
                with col2:
                    st.metric("Study", study_title[:30] + "..." if len(study_title) > 30 else study_title)
                with col3:
                    st.metric("Duration", f"{duration} min")

                final_df = pd.DataFrame([{
                    "email": e["email"],
                    "first_name": e["first_name"],
                    "user_type": e["user_type"],
                    "invited": True,
                } for e in emails])

                csv = final_df.to_csv(index=False)
                st.download_button(
                    "📥 Download invite list (CSV)",
                    csv,
                    file_name=f"invited_{study_title[:20].replace(' ','_')}_{date.today()}.csv",
                    mime="text/csv",
                )
    render_step_footer()

# ── Main router ────────────────────────────────────────────────────────────────
def main():
    sidebar()

    step = st.session_state["step"]

    if step == 1:
        step_sign_in()
    elif step == 2:
        step_load_candidates()
    elif step == 3:
        step_sample_design()
    elif step == 4:
        step_calendar_setup()
    elif step == 5:
        step_generate_and_send()

if __name__ == "__main__":
    main()
