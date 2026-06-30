"""
=======================================================
  ROVER RESEARCH OPS — Interview Scheduler
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

REDIRECT_URI = st.secrets.get("redirect_uri", "http://localhost:8501")
CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"

BASE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1hmLE712LyJPu6_LK5o9O9OyeoAeOHuK9VyhI9-9u1k0/edit?usp=sharing"

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
        "is_base_sheet": False,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

init_session_state()


# ── Styling ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

    .step-header {
        font-size: 1.35rem; font-weight: 700; color: #111827;
        margin-bottom: 0.15rem; letter-spacing: -0.01em;
    }
    .step-sub {
        font-size: 0.9rem; color: #6b7280; margin-bottom: 1.5rem;
    }
    .stat-box {
        background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
        padding: 1.1rem 1rem; text-align: center;
        box-shadow: 0 1px 3px rgba(0,0,0,.04);
    }
    .stat-num  { font-size: 1.9rem; font-weight: 800; color: #7C3AED; line-height: 1.1; }
    .stat-label { font-size: 0.75rem; color: #9ca3af; margin-top: 2px; text-transform: uppercase; letter-spacing: .04em; }
    .var-panel {
        background: #faf5ff; border: 1px solid #e9d5ff; border-radius: 10px;
        padding: 0.85rem 1rem; margin-bottom: 0.5rem;
    }
    .var-pill {
        display: inline-block; background: #ede9fe; color: #5b21b6;
        border-radius: 6px; padding: 2px 8px; margin: 2px 3px 2px 0;
        font-family: 'Courier New', monospace; font-size: 0.78rem; font-weight: 600;
        cursor: default;
    }
    .section-label {
        font-size: 0.72rem; font-weight: 600; color: #9ca3af;
        text-transform: uppercase; letter-spacing: .06em; margin-bottom: 0.4rem;
    }
    .tz-card {
        background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px;
        padding: 0.8rem 1rem; margin-bottom: 0.5rem; color: #14532d;
        font-size: 0.9rem;
    }
    .tz-warn {
        background: #fffbeb; border: 1px solid #fde68a; border-radius: 10px;
        padding: 0.8rem 1rem; margin-bottom: 0.5rem; color: #78350f;
        font-size: 0.9rem;
    }
    .success-banner {
        background: #f0fdf4; border: 1px solid #86efac; border-radius: 10px;
        padding: 0.8rem 1.1rem; color: #166534; font-size: 0.9rem;
    }
    div[data-testid="stExpander"] { border: 1px solid #e5e7eb !important; border-radius: 10px !important; }
    div[data-testid="stExpander"] summary { font-weight: 600 !important; }
    .stButton > button[kind="primary"] {
        background: #7C3AED !important; border-color: #7C3AED !important;
        border-radius: 8px !important; font-weight: 600 !important;
    }
    .stButton > button[kind="primary"]:hover { background: #6d28d9 !important; }
</style>
""", unsafe_allow_html=True)

# ── OAuth helpers ──────────────────────────────────────────────────────────────
def get_flow():
    return Flow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
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
    verifier, challenge = _pkce_pair()
    # Write to file — session_state won't survive the Google redirect
    PKCE_FILE.write_text(verifier)

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    st.session_state["oauth_state"] = state
    return auth_url

def exchange_code_for_credentials(code):
    flow = get_flow()
    verifier = PKCE_FILE.read_text().strip() if PKCE_FILE.exists() else None
    PKCE_FILE.unlink(missing_ok=True)
    flow.fetch_token(code=code, code_verifier=verifier)
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
        st.image("https://www.rover.com/blog/wp-content/uploads/rover-logo-black.png", width=120)
        st.markdown("### Interview Scheduler")
        st.markdown("---")

        steps = [
            (1, "Sign in with Google"),
            (2, "Select candidates"),
            (3, "Design invitation"),
            (4, "Set up calendar"),
            (5, "Send invites"),
        ]

        current = st.session_state["step"]
        # Allow jumping to any step that's been reached or is current
        max_reached = current
        for n, label in steps:
            if n < current:
                icon = "✅"
            elif n == current:
                icon = "👉"
            else:
                icon = "○"

            # Steps already visited are clickable; future steps are greyed out
            if n <= max_reached:
                if st.button(f"{icon} Step {n} — {label}", key=f"nav_{n}", use_container_width=True):
                    st.session_state["step"] = n
                    st.rerun()
            else:
                st.markdown(f"<span style='color:#9ca3af'>{icon} Step {n} — {label}</span>", unsafe_allow_html=True)

        st.markdown("---")
        if st.session_state["user_email"]:
            st.caption(f"Signed in as\n**{st.session_state['user_email']}**")

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
    st.markdown("<div style='height:2.5rem'></div>", unsafe_allow_html=True)
    pills = ""
    for n, label in steps:
        if n < current:
            bg, fg, dot = "#7C3AED", "#fff", "✓"
        elif n == current:
            bg, fg, dot = "#7C3AED", "#fff", str(n)
        else:
            bg, fg, dot = "#e5e7eb", "#9ca3af", str(n)
        txt_color = "#111827" if n == current else ("#7C3AED" if n < current else "#9ca3af")
        weight = "700" if n == current else "400"
        pills += (
            f'<div style="display:flex;align-items:center;gap:6px">'
            f'<span style="width:22px;height:22px;border-radius:50%;background:{bg};color:{fg};'
            f'display:flex;align-items:center;justify-content:center;font-size:0.65rem;font-weight:700;flex-shrink:0">{dot}</span>'
            f'<span style="font-size:0.75rem;color:{txt_color};font-weight:{weight};white-space:nowrap">{label}</span>'
            f'</div>'
        )
        if n < len(steps):
            pills += '<div style="flex:1;height:1px;background:#e5e7eb;min-width:12px;margin:0 4px"></div>'
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:0;padding:0.9rem 1rem;'
        f'background:#fff;border:1px solid #e5e7eb;border-radius:12px;margin-top:0.5rem">'
        f'{pills}</div>',
        unsafe_allow_html=True,
    )

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

    st.markdown('<div class="step-header">Selecciona tus candidatos</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-sub">Carga el pool, aplica filtros y elige cuántos necesitas para tu estudio.</div>', unsafe_allow_html=True)

    def stat_box(value, label):
        display = f"{value:,}" if isinstance(value, int) else str(value)
        return f'<div class="stat-box"><div class="stat-num">{display}</div><div class="stat-label">{label}</div></div>'

    def is_recent_incentive(val, months=6):
        """True si el incentivo fue recibido en los últimos `months` meses."""
        if pd.isna(val) or str(val).strip() in ("", "None", "nan", "0"):
            return False
        try:
            dt = pd.to_datetime(val, dayfirst=False)
            cutoff = datetime.now() - timedelta(days=months * 30)
            return dt.replace(tzinfo=None) >= cutoff
        except Exception:
            return False

    # ── Load data ──────────────────────────────────────────────────────────────
    if st.session_state["df_candidates"] is None:
        st.markdown(
            '<div style="background:#faf5ff;border:1px solid #e9d5ff;border-radius:10px;'
            'padding:0.85rem 1.1rem;margin-bottom:1.2rem;font-size:0.9rem">'
            '📊 <strong>Base oficial de Research Ops</strong> — Contiene todos los usuarios de Rover '
            'con sus segmentos, servicios e historial UX. Al cargarla, se excluyen automáticamente '
            'personas con incentivo UX en los últimos 6 meses o con más de 5 incentivos acumulados.'
            '</div>',
            unsafe_allow_html=True,
        )

        tab_base, tab_sheet, tab_csv = st.tabs(["📊 Base de candidatos Rover (recomendado)", "🔗 Otra Google Sheet", "📁 Subir CSV"])

        with tab_base:
            st.caption("La spreadsheet oficial de Research Ops, con segmentos, historial UX y servicios de cada usuario.")
            if st.button("Cargar base de candidatos", type="primary", key="load_base"):
                with st.spinner("Cargando base de candidatos..."):
                    try:
                        creds = dict_to_creds(st.session_state["credentials"])
                        gc = gspread.authorize(creds)
                        sh = gc.open_by_url(BASE_SHEET_URL)
                        df = pd.DataFrame(sh.get_worksheet(0).get_all_records())
                        st.session_state["df_candidates"] = df
                        st.session_state["is_base_sheet"] = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"No se pudo leer la sheet: {e}")

        with tab_sheet:
            st.caption("Pega la URL de cualquier otra Google Sheet exportada desde Mode u otra fuente.")
            sheet_url = st.text_input("Sheet URL", placeholder="https://docs.google.com/spreadsheets/d/...", label_visibility="collapsed")
            if st.button("Cargar", key="load_sheet") and sheet_url:
                with st.spinner("Cargando..."):
                    try:
                        creds = dict_to_creds(st.session_state["credentials"])
                        gc = gspread.authorize(creds)
                        sh = gc.open_by_url(sheet_url)
                        df = pd.DataFrame(sh.get_worksheet(0).get_all_records())
                        st.session_state["df_candidates"] = df
                        st.session_state["is_base_sheet"] = False
                        st.rerun()
                    except Exception as e:
                        st.error(f"No se pudo leer la sheet: {e}")

        with tab_csv:
            st.caption("Sube un CSV exportado desde Mode.")
            uploaded = st.file_uploader("Upload CSV", type=["csv"], label_visibility="collapsed")
            if uploaded:
                st.session_state["df_candidates"] = pd.read_csv(uploaded)
                st.session_state["is_base_sheet"] = False
                st.rerun()
        return

    df = st.session_state["df_candidates"].copy()
    is_base = st.session_state.get("is_base_sheet", False)

    # ── Auto-exclusiones (solo base sheet) ────────────────────────────────────
    if is_base:
        excl_recent = 0
        excl_many = 0

        if "most_recent_ux_incentive" in df.columns:
            mask = df["most_recent_ux_incentive"].apply(is_recent_incentive)
            excl_recent = int(mask.sum())
            df = df[~mask]

        if "total_ux_incentives" in df.columns:
            nums = pd.to_numeric(df["total_ux_incentives"], errors="coerce").fillna(0)
            mask = nums > 5
            excl_many = int(mask.sum())
            df = df[~mask]

        if excl_recent > 0 or excl_many > 0:
            parts = []
            if excl_recent:
                parts.append(f"<strong>{excl_recent}</strong> con incentivo UX en los últimos 6 meses")
            if excl_many:
                parts.append(f"<strong>{excl_many}</strong> con más de 5 incentivos UX acumulados")
            st.markdown(
                f'<div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;'
                f'padding:0.7rem 1rem;margin-bottom:0.75rem;color:#9a3412;font-size:0.88rem">'
                f'🚫 Excluidos automáticamente: {" y ".join(parts)}.</div>',
                unsafe_allow_html=True,
            )

    # ── Pool stats ─────────────────────────────────────────────────────────────
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.markdown(stat_box(len(df), "Total"), unsafe_allow_html=True)
    with col2:
        owners = int((df["user_type"].str.lower() == "owner").sum()) if "user_type" in df.columns else "—"
        st.markdown(stat_box(owners, "Owners"), unsafe_allow_html=True)
    with col3:
        sitters = int((df["user_type"].str.lower() == "sitter").sum()) if "user_type" in df.columns else "—"
        st.markdown(stat_box(sitters, "Sitters"), unsafe_allow_html=True)
    with col4:
        segs = int(df["grouped_segment"].nunique()) if "grouped_segment" in df.columns else "—"
        st.markdown(stat_box(segs, "Segments"), unsafe_allow_html=True)
    with col5:
        if st.button("↺ Cambiar datos", use_container_width=True):
            st.session_state["df_candidates"] = None
            st.session_state["df_sample"] = None
            st.session_state["is_base_sheet"] = False
            st.rerun()

    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)

    # ── Filtros ────────────────────────────────────────────────────────────────
    with st.expander("Filtros", expanded=True):
        col1, col2 = st.columns(2)

        with col1:
            # Tipo de usuario
            user_type_sel = "Todos"
            if "user_type" in df.columns:
                st.markdown("**👤 Tipo de usuario**")
                st.caption(
                    "**Owner**: dueño de mascota que contrata servicios (hospedaje, paseos, guardería). "
                    "**Sitter**: proveedor de servicios que cuida mascotas."
                )
                opts = ["Todos"] + sorted(df["user_type"].dropna().unique().tolist())
                user_type_sel = st.selectbox("Tipo de usuario", opts, label_visibility="collapsed")

            # Segmento agrupado
            segment_sel = []
            if "grouped_segment" in df.columns:
                st.markdown("**📊 Segmento de actividad**")
                st.caption(
                    "Agrupación del comportamiento del usuario según su frecuencia de uso: "
                    "**Core** = usuarios regulares y activos · "
                    "**Infrequent** = uso esporádico · "
                    "**Churn** = sin actividad reciente · "
                    "**New** = se unieron recientemente."
                )
                segment_sel = st.multiselect(
                    "Segmento", sorted(df["grouped_segment"].dropna().unique().tolist()),
                    label_visibility="collapsed",
                )

            # Segmento detallado
            segment_raw_sel = []
            if "segment" in df.columns:
                st.markdown("**🔍 Segmento detallado**")
                st.caption("Versión más granular del segmento. Útil si quieres enfocarte en un perfil muy específico.")
                segment_raw_sel = st.multiselect(
                    "Segment detallado", sorted(df["segment"].dropna().unique().tolist()),
                    label_visibility="collapsed",
                )

            # Etapa del proveedor
            provider_stage_sel = []
            if "provider_stage" in df.columns:
                st.markdown("**🚀 Etapa del sitter**")
                st.caption(
                    "Fase del ciclo de vida del sitter dentro de la plataforma: "
                    "desde recién registrado hasta sitters con alto volumen de reservas."
                )
                provider_stage_sel = st.multiselect(
                    "Etapa", sorted(df["provider_stage"].dropna().unique().tolist()),
                    label_visibility="collapsed",
                )

            # Reservas últimos 12 meses
            booking_range = None
            if "total_bookings_l12" in df.columns:
                st.markdown("**📅 Reservas en los últimos 12 meses**")
                st.caption(
                    "Número total de reservas completadas en el último año. "
                    "Filtra por rango para enfocarte en usuarios muy activos o, al contrario, "
                    "en quienes llevan tiempo sin reservar."
                )
                nums = pd.to_numeric(df["total_bookings_l12"], errors="coerce")
                b_min, b_max = int(nums.min() or 0), int(nums.max() or 0)
                if b_min < b_max:
                    booking_range = st.slider(
                        "Rango de reservas", b_min, b_max, (b_min, b_max),
                        label_visibility="collapsed",
                    )

        with col2:
            # Servicios que ofrece el sitter
            st.markdown("**🐶 Servicios que ofrece (sitters)**")
            st.caption(
                "Filtra sitters según los servicios que tienen habilitados en su perfil. "
                "Útil si tu estudio es sobre un servicio específico."
            )

            boarding_sel = "Todos"
            if "offers_boarding" in df.columns:
                boarding_sel = st.selectbox(
                    "🏠 Hospedaje (Boarding) — el sitter recibe mascotas en su casa de noche",
                    ["Todos", "Sí ofrece", "No ofrece"],
                )

            daycare_sel = "Todos"
            if "offers_day_care" in df.columns:
                daycare_sel = st.selectbox(
                    "☀️ Guardería (Day Care) — cuidan la mascota durante el día",
                    ["Todos", "Sí ofrece", "No ofrece"],
                )

            walking_sel = "Todos"
            if "offers_dog_walking" in df.columns:
                walking_sel = st.selectbox(
                    "🦮 Paseo de perros (Dog Walking)",
                    ["Todos", "Sí ofrece", "No ofrece"],
                )

            # Incentivos UX (solo visible si no es base sheet, donde ya se filtra auto)
            incentive_max = None
            if "total_ux_incentives" in df.columns and not is_base:
                st.markdown("**🎁 Incentivos UX acumulados**")
                st.caption(
                    "Número de veces que el usuario ha participado en investigación UX y recibido un incentivo. "
                    "En la base oficial esto ya se filtra automáticamente (máx. 5). "
                    "Si usas otra fuente, puedes ajustar el límite aquí."
                )
                i_max = int(pd.to_numeric(df["total_ux_incentives"], errors="coerce").max() or 0)
                if i_max > 0:
                    incentive_max = st.slider(
                        "Máximo de incentivos recibidos", 0, i_max, i_max,
                        label_visibility="collapsed",
                    )

        # Aplicar filtros
        filtered = df.copy()

        if user_type_sel != "Todos" and "user_type" in filtered.columns:
            filtered = filtered[filtered["user_type"].str.lower() == user_type_sel.lower()]
        if segment_sel and "grouped_segment" in filtered.columns:
            filtered = filtered[filtered["grouped_segment"].isin(segment_sel)]
        if segment_raw_sel and "segment" in filtered.columns:
            filtered = filtered[filtered["segment"].isin(segment_raw_sel)]
        if provider_stage_sel and "provider_stage" in filtered.columns:
            filtered = filtered[filtered["provider_stage"].isin(provider_stage_sel)]
        if booking_range and "total_bookings_l12" in filtered.columns:
            nums = pd.to_numeric(filtered["total_bookings_l12"], errors="coerce")
            filtered = filtered[(nums >= booking_range[0]) & (nums <= booking_range[1])]
        if boarding_sel != "Todos" and "offers_boarding" in filtered.columns:
            v = 1 if boarding_sel == "Sí ofrece" else 0
            filtered = filtered[pd.to_numeric(filtered["offers_boarding"], errors="coerce") == v]
        if daycare_sel != "Todos" and "offers_day_care" in filtered.columns:
            v = 1 if daycare_sel == "Sí ofrece" else 0
            filtered = filtered[pd.to_numeric(filtered["offers_day_care"], errors="coerce") == v]
        if walking_sel != "Todos" and "offers_dog_walking" in filtered.columns:
            v = 1 if walking_sel == "Sí ofrece" else 0
            filtered = filtered[pd.to_numeric(filtered["offers_dog_walking"], errors="coerce") == v]
        if incentive_max is not None and "total_ux_incentives" in filtered.columns:
            nums = pd.to_numeric(filtered["total_ux_incentives"], errors="coerce")
            filtered = filtered[nums <= incentive_max]

        if len(filtered) == 0:
            st.warning("No hay candidatos que cumplan estos filtros. Prueba a ampliar la selección.")
            return

        breakdown = ""
        if "user_type" in filtered.columns:
            breakdown = " · ".join(f"{v:,} {k}s" for k, v in filtered["user_type"].value_counts().items())
        st.caption(f"Pool elegible: **{len(filtered):,}** candidatos — {breakdown}")

    # ── Warnings ───────────────────────────────────────────────────────────────
    warnings_shown = False

    if "grouped_segment" in filtered.columns:
        risky = filtered[filtered["grouped_segment"].str.lower().isin(["infrequent", "churn"])]
        if len(risky) > 0:
            pct = int(round(len(risky) / len(filtered) * 100))
            st.markdown(
                f'<div class="tz-warn">⚠️ <strong>{len(risky)} candidatos ({pct}%)</strong> tienen '
                f'segmento <strong>Infrequent o Churn</strong> — usuarios con poca o ninguna actividad reciente. '
                f'Pueden tener menor tasa de respuesta o una experiencia que ya no refleja el uso actual de Rover. '
                f'Considera si son el perfil adecuado para tu estudio.</div>',
                unsafe_allow_html=True,
            )
            warnings_shown = True

    if "user_type" in filtered.columns and "total_bookings_l12" in filtered.columns:
        owner_mask = filtered["user_type"].str.lower() == "owner"
        zero_mask = pd.to_numeric(filtered["total_bookings_l12"], errors="coerce").fillna(-1) == 0
        inactive_owners = filtered[owner_mask & zero_mask]
        if len(inactive_owners) > 0:
            pct = int(round(len(inactive_owners) / len(filtered) * 100))
            st.markdown(
                f'<div class="tz-warn">⚠️ <strong>{len(inactive_owners)} owners ({pct}%)</strong> '
                f'no han tenido ninguna reserva en los últimos 12 meses — puede que ya no estén usando Rover activamente. '
                f'Sus opiniones pueden diferir bastante de las de owners frecuentes.</div>',
                unsafe_allow_html=True,
            )
            warnings_shown = True

    if not warnings_shown:
        st.markdown(
            '<div class="tz-card">✅ Sin señales de alerta — todos los candidatos del pool parecen elegibles.</div>',
            unsafe_allow_html=True,
        )

    # ── Cuántos necesitas + duración ───────────────────────────────────────────
    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
    st.markdown('<div class="section-label">¿Cuántos necesitas?</div>', unsafe_allow_html=True)

    col_n, col_dur, col_btn = st.columns([1, 1, 2])
    with col_n:
        n_interviews = st.number_input(
            "Entrevistas", min_value=1,
            max_value=min(len(filtered), 500),
            value=min(15, len(filtered)), step=1,
            label_visibility="collapsed",
        )
    with col_dur:
        dur = st.selectbox(
            "Duración", [30, 45, 60], index=1,
            format_func=lambda x: f"{x} min",
            label_visibility="collapsed",
        )
        st.session_state["interview_duration"] = dur
    with col_btn:
        st.markdown("<div style='margin-top:0.35rem'></div>", unsafe_allow_html=True)
        if st.button(f"Seleccionar {n_interviews} candidatos al azar", type="primary", use_container_width=True):
            sample = filtered.sample(n=min(n_interviews, len(filtered)), random_state=None)
            st.session_state["df_sample"] = sample.reset_index(drop=True)
            st.rerun()

    if st.session_state["df_sample"] is not None:
        sample = st.session_state["df_sample"]
        cols_to_show = [c for c in [
            "first_name", "email", "user_type", "grouped_segment",
            "total_bookings_l12", "total_ux_incentives",
        ] if c in sample.columns]
        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
        st.dataframe(sample[cols_to_show] if cols_to_show else sample, use_container_width=True, hide_index=True)
        st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)
        if st.button("Continuar →", type="primary"):
            st.session_state["step"] = 3
            st.rerun()
    render_step_footer()


# ── STEP 3 — Design invitation ─────────────────────────────────────────────────
def step_sample_design():
    import pytz

    st.markdown('<div class="step-header">Design your invitation</div>', unsafe_allow_html=True)
    st.markdown('<div class="step-sub">Set the study title, write your email template, and find the best scheduling windows for your candidates.</div>', unsafe_allow_html=True)

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
    st.markdown('<div class="section-label">Study title</div>', unsafe_allow_html=True)
    current_title = st.session_state.get("study_title") or ""
    default_idx = TITLE_OPTIONS.index(current_title) if current_title in TITLE_OPTIONS else len(TITLE_OPTIONS) - 1
    selected_title = st.selectbox("Title", TITLE_OPTIONS, index=default_idx, label_visibility="collapsed")
    if selected_title == "Custom...":
        custom_title = st.text_input(
            "Custom title",
            value=current_title if current_title not in TITLE_OPTIONS else "",
            placeholder="e.g. Sitter Experience Research — June 2026",
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

We're looking to speak with Rover [user_type]s about their experiences. As a thank-you for your time, you'll receive [incentive_currency][incentive_amount] added directly to your Rover account.

What to expect:
• [duration]-minute conversation via Google Meet
• Join from a quiet place with audio and video enabled
• No preparation needed

Interested? Pick a time that works for you: [BOOKING_LINK]

If all sessions are full, feel free to reply to this email and we'll add you to the waitlist for future studies.

Thank you for helping us improve the Rover experience!

Best,
Rover Research Team"""

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

        COUNTRY_TZ = {
            "US": ["America/New_York", "America/Chicago", "America/Los_Angeles"],
            "CA": ["America/Toronto", "America/Vancouver"],
            "GB": ["Europe/London"],
            "DE": ["Europe/Berlin"],
            "FR": ["Europe/Paris"],
            "NL": ["Europe/Amsterdam"],
            "AU": ["Australia/Sydney"],
            "NZ": ["Pacific/Auckland"],
            "NO": ["Europe/Oslo"],
            "SE": ["Europe/Stockholm"],
            "ES": ["Europe/Madrid"],
            "MX": ["America/Mexico_City"],
            "BR": ["America/Sao_Paulo"],
            "IT": ["Europe/Rome"],
        }

        rtz = pytz.timezone(researcher_tz)
        now = datetime.now(rtz).replace(minute=0, second=0, microsecond=0)
        country_counts = sample["country_code"].dropna().value_counts()
        total = len(sample)

        st.markdown(f'<div class="section-label">Scheduling windows — {total} candidates selected</div>', unsafe_allow_html=True)

        global_good = set(range(9, 18))

        for country, count in country_counts.items():
            if country not in COUNTRY_TZ:
                continue
            pct = int(round(count / total * 100))

            good_hours = []
            for ctz_str in COUNTRY_TZ[country]:
                ctz = pytz.timezone(ctz_str)
                for hour in range(7, 20):
                    r_hour = now.replace(hour=hour)
                    c_hour = r_hour.astimezone(ctz)
                    if 9 <= c_hour.hour <= 17 and 9 <= r_hour.hour <= 17:
                        good_hours.append(r_hour.hour)
                if good_hours:
                    break

            if good_hours:
                window = f"{min(good_hours):02d}:00 – {max(good_hours):02d}:00"
                global_good &= set(good_hours)
                st.markdown(
                    f'<div class="tz-card">🌍 <strong>{country}</strong> &nbsp;·&nbsp; {count} candidate{"s" if count > 1 else ""} ({pct}%)'
                    f'<br><span style="font-size:0.82rem">Overlap window in your timezone: <strong>{window}</strong></span></div>',
                    unsafe_allow_html=True,
                )
            else:
                global_good = set()
                st.markdown(
                    f'<div class="tz-warn">⚠️ <strong>{country}</strong> &nbsp;·&nbsp; {count} candidate{"s" if count > 1 else ""} ({pct}%)'
                    f'<br><span style="font-size:0.82rem">No easy working-hours overlap — consider early morning or async scheduling.</span></div>',
                    unsafe_allow_html=True,
                )

        if global_good:
            best_start = f"{min(global_good):02d}:00"
            best_end   = f"{max(global_good):02d}:00"
            st.markdown(
                f'<div style="background:#f5f3ff;border:1px solid #ddd6fe;border-radius:10px;padding:0.8rem 1rem;margin-top:0.4rem;color:#4c1d95">'
                f'💡 <strong>Best window across all candidates:</strong> {best_start} – {best_end} your time</div>',
                unsafe_allow_html=True,
            )

    # ── Continue ───────────────────────────────────────────────────────────────
    st.markdown("<div style='height:1rem'></div>", unsafe_allow_html=True)
    if not st.session_state.get("study_title"):
        st.info("Add a study title to continue.")
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

    import pytz

    # ── Candidate timezone context ─────────────────────────────────────────────
    COUNTRY_TZ = {
        "US": ("America/New_York", "United States"),
        "CA": ("America/Toronto", "Canada"),
        "GB": ("Europe/London", "United Kingdom"),
        "DE": ("Europe/Berlin", "Germany"),
        "FR": ("Europe/Paris", "France"),
        "NL": ("Europe/Amsterdam", "Netherlands"),
        "AU": ("Australia/Sydney", "Australia"),
        "NZ": ("Pacific/Auckland", "New Zealand"),
        "NO": ("Europe/Oslo", "Norway"),
        "SE": ("Europe/Stockholm", "Sweden"),
        "ES": ("Europe/Madrid", "Spain"),
        "MX": ("America/Mexico_City", "Mexico"),
        "BR": ("America/Sao_Paulo", "Brazil"),
        "IT": ("Europe/Rome", "Italy"),
    }

    if sample is not None and "country_code" in sample.columns:
        rtz = pytz.timezone(researcher_tz)
        now = datetime.now(rtz).replace(minute=0, second=0, microsecond=0)
        country_counts = sample["country_code"].dropna().value_counts()
        known = [(c, n) for c, n in country_counts.items() if c in COUNTRY_TZ]

        if known:
            lines = []
            global_good = set(range(9, 18))
            for country, count in known:
                ctz_str, cname = COUNTRY_TZ[country]
                ctz = pytz.timezone(ctz_str)
                good = []
                for hour in range(7, 20):
                    r = now.replace(hour=hour)
                    c = r.astimezone(ctz)
                    if 9 <= c.hour <= 17 and 9 <= r.hour <= 17:
                        good.append(r.hour)
                if good:
                    global_good &= set(good)
                    window = f"{min(good):02d}:00–{max(good):02d}:00"
                    lines.append(f"• **{cname}** ({count} candidate{'s' if count > 1 else ''}) → working-hours overlap in your timezone: **{window}**")
                else:
                    global_good = set()
                    lines.append(f"• **{cname}** ({count} candidate{'s' if count > 1 else ''}) → ⚠️ difficult overlap")

            rec = f"\n\n💡 **Recommended window covering all candidates:** {min(global_good):02d}:00–{max(global_good):02d}:00 your time" if global_good else ""

            st.markdown('<div class="section-label">Your candidates\' timezones</div>', unsafe_allow_html=True)
            st.markdown(
                '<div style="background:#faf5ff;border:1px solid #e9d5ff;border-radius:10px;padding:0.9rem 1.1rem;margin-bottom:1rem">'
                + "When setting up your time slots, keep these windows in mind:<br><br>"
                + "<br>".join(lines)
                + rec.replace("\n\n", "<br><br>").replace("\n", "<br>")
                + "</div>",
                unsafe_allow_html=True,
            )

    # ── Copy-paste content ─────────────────────────────────────────────────────
    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown('<div class="section-label">Appointment title</div>', unsafe_allow_html=True)
        st.code(study_title or "Your study title here", language=None)

    with col_r:
        st.markdown('<div class="section-label">Description template</div>', unsafe_allow_html=True)
        DESCRIPTION_TEMPLATES = {
            "Short": (
                "Pick a day and time that works best for you! 😊\n\n"
                "Note: this calendar detects your time zone automatically — "
                "just double-check before confirming."
            ),
            "Full invite": (
                f"What to expect:\n"
                f"• {duration}-minute conversation via Google Meet\n"
                "• Join from a quiet place with audio and video enabled\n"
                "• No preparation needed\n\n"
                "Interested? Pick a time that works for you.\n\n"
                "If all sessions are full, reply to this email and we'll add you to the waitlist."
            ),
        }
        selected_desc = st.radio("Template", list(DESCRIPTION_TEMPLATES.keys()), horizontal=True, label_visibility="collapsed")
        if st.session_state.get("_last_desc_template") != selected_desc:
            st.session_state["calendar_description"] = DESCRIPTION_TEMPLATES[selected_desc]
            st.session_state["_last_desc_template"] = selected_desc
        calendar_description = st.text_area(
            "Description",
            value=st.session_state.get("calendar_description", DESCRIPTION_TEMPLATES[selected_desc]),
            height=120,
            label_visibility="collapsed",
            key="cal_desc_editor",
        )
        st.session_state["calendar_description"] = calendar_description
        st.code(calendar_description, language=None)

    st.markdown("<div style='height:0.75rem'></div>", unsafe_allow_html=True)

    # ── Instructions + open button ─────────────────────────────────────────────
    st.markdown('<div class="section-label">Steps in Google Calendar</div>', unsafe_allow_html=True)
    col_steps, col_btn = st.columns([4, 1])
    with col_steps:
        st.markdown(
            f"1. Click **Open Google Calendar** → **+ Create** → **Appointment schedule**  \n"
            f"2. Paste the **title** above as the schedule name  \n"
            f"3. Set duration to **{duration} min** · timezone to **{researcher_tz}**  \n"
            f"4. Add your available days and times — keep the windows above in mind  \n"
            f"5. Paste the **description** above in the description field  \n"
            f"6. Click **Save** → open the schedule page → copy the **Open booking page** link  \n"
            f"7. Paste the link below and click **Save**"
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
