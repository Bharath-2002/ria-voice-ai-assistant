"""Ria — Call Evaluation Dashboard (Streamlit).

Run:  uv run --extra eval streamlit run eval/dashboard.py
Env:  ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, DATABASE_URL, GEMINI_API_KEY
"""

import datetime as dt
import os
import sys

# allow `from eval import ...` when run via `streamlit run eval/dashboard.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import httpx
import pandas as pd
import streamlit as st

from eval import elevenlabs_api as el
from eval import store
from eval.rubric import DIMENSIONS
from eval.validator import validate

st.set_page_config(page_title="Ria — Call Evaluations", layout="wide")

RIA_APP_URL = os.environ.get("RIA_APP_URL", "https://ria-app-production.up.railway.app").rstrip("/")

_DARK_CSS = """
<style>
:root, .stApp { background-color:#0e1117 !important; color:#e6e6e6 !important; }
section[data-testid="stSidebar"] { background-color:#161a23 !important; }
.stApp, .stApp p, .stApp span, .stApp label, .stApp li, .stApp h1, .stApp h2, .stApp h3,
.stApp h4, .stApp .stMarkdown { color:#e6e6e6 !important; }
[data-testid="stDataFrame"] div, [data-testid="stDataFrame"] td, [data-testid="stDataFrame"] th { color:#e6e6e6 !important; }
[data-testid="stMetricValue"], [data-testid="stMetricLabel"] { color:#e6e6e6 !important; }
.stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] { background-color:#1c2230 !important; color:#e6e6e6 !important; }
div[data-testid="stExpander"] { background-color:#161a23 !important; border-color:#2a3140 !important; }
</style>
"""


def apply_theme():
    if "theme" not in st.session_state:
        st.session_state["theme"] = "Light"
    with st.sidebar:
        st.session_state["theme"] = st.radio(
            "🎨 Theme", ["Light", "Dark"],
            index=0 if st.session_state["theme"] == "Light" else 1,
            horizontal=True,
        )
    if st.session_state["theme"] == "Dark":
        st.markdown(_DARK_CSS, unsafe_allow_html=True)


def outbound_call_panel():
    """Sidebar panel: trigger an outbound call to a +91 number via the ria-app backend."""
    with st.sidebar:
        st.divider()
        st.markdown("### 📞 Trigger an outbound call")
        cc, num = st.columns([1, 2])
        with cc:
            country = st.selectbox("Code", ["+91", "+1", "+44", "+971"], index=0, label_visibility="collapsed")
        with num:
            digits = st.text_input("Number", placeholder="98765 43210", label_visibility="collapsed")
        digits_clean = "".join(ch for ch in (digits or "") if ch.isdigit())
        to_number = f"{country}{digits_clean}" if digits_clean else ""
        if st.button("Call now", disabled=not digits_clean, width="stretch"):
            try:
                r = httpx.post(f"{RIA_APP_URL}/voice/outbound", json={"to_number": to_number}, timeout=20)
                if r.status_code < 300:
                    st.success(f"Calling {to_number} — Ria will ring you shortly.")
                else:
                    st.error(f"Call failed ({r.status_code}): {r.text[:200]}")
            except Exception as e:
                st.error(f"Could not reach the backend: {e}")
        if to_number:
            st.caption(f"Will dial: `{to_number}`")


# ----------------------------------------------------------------- data loading

@st.cache_data(ttl=60, show_spinner=False)
def load_calls(limit: int = 100):
    rows = el.list_conversations(limit=limit)
    out = []
    for c in rows:
        cid = c.get("conversation_id") or c.get("id")
        ts = c.get("start_time_unix_secs") or c.get("created_at_unix_secs")
        out.append({
            "conversation_id": cid,
            "direction": el.conversation_direction(c),
            "started_at": dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "",
            "duration_secs": c.get("call_duration_secs") or c.get("duration_secs") or 0,
            "turns": c.get("message_count") or 0,
            "status": c.get("status") or c.get("call_successful") or "",
        })
    return out


def load_evals():
    try:
        return store.latest_by_conversation()
    except Exception as e:
        st.warning(f"Could not read evaluations from Postgres: {e}")
        return {}


def fmt_dur(secs: int) -> str:
    secs = int(secs or 0)
    return f"{secs // 60}m{secs % 60:02d}s"


# ------------------------------------------------------------------- validation

# validate() is I/O-bound (ElevenLabs fetch + Gemini judge + Postgres write), so a
# small thread pool gives ~Nx speed-up on batch runs. Kept small to stay under the
# Gemini RPM limit and the SQLAlchemy connection pool size.
_VALIDATION_WORKERS = int(os.environ.get("EVAL_PARALLELISM", "4"))


def _validate_and_store(cid: str):
    payload = validate(cid)
    store.save_evaluation(payload)
    return cid


def run_validation(conversation_ids: list[str]):
    import concurrent.futures as cf

    n = len(conversation_ids)
    prog = st.progress(0.0, text=f"Validating 0/{n}…")
    ok, fail, done = 0, 0, 0
    workers = max(1, min(_VALIDATION_WORKERS, n))
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_validate_and_store, cid): cid for cid in conversation_ids}
        for fut in cf.as_completed(futures):
            cid = futures[fut]
            done += 1
            try:
                fut.result()
                ok += 1
            except Exception as e:
                fail += 1
                st.error(f"{cid}: {e}")
            prog.progress(done / n, text=f"Validating {done}/{n}…")
    prog.empty()
    st.success(f"Done — {ok} validated{', ' + str(fail) + ' failed' if fail else ''} (×{workers} parallel).")
    load_calls.clear()


# --------------------------------------------------------------------- UI: list

def page_list():
    st.title("Ria — Call Evaluations")
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    with c1:
        if st.button("🔄 Refresh calls"):
            load_calls.clear()
            st.rerun()
    with c2:
        dir_filter = st.selectbox("Direction", ["all", "inbound", "outbound", "unknown"])
    with c3:
        eval_filter = st.selectbox("Eval status", ["all", "validated", "not validated", "passed", "failed"])
    with c4:
        limit = st.number_input("Load N calls", 10, 200, 100, step=10)

    calls = load_calls(int(limit))
    evals = load_evals()

    rows = []
    for c in calls:
        e = evals.get(c["conversation_id"])
        status = "⚪ Not validated"
        score = None
        if e:
            status = "✅ Passed" if e["overall_passed"] else "❌ Failed"
            score = e["overall_score"]
        if dir_filter != "all" and c["direction"] != dir_filter:
            continue
        if eval_filter == "validated" and not e: continue
        if eval_filter == "not validated" and e: continue
        if eval_filter == "passed" and not (e and e["overall_passed"]): continue
        if eval_filter == "failed" and not (e and not e["overall_passed"]): continue
        rows.append({
            "select": False,
            "conversation": c["conversation_id"],          # rendered as a link → ?call=<id>
            "_cid": c["conversation_id"],                   # plain id for selection logic
            "direction": c["direction"],
            "started_at": c["started_at"],
            "duration": fmt_dur(c["duration_secs"]),
            "turns": c["turns"],
            "eval": status,
            "score": score,
        })

    if not rows:
        st.info("No calls match the filters. (If the list is empty, check ELEVENLABS_API_KEY / agent id.)")
        return

    df = pd.DataFrame(rows)
    df_view = df.copy()
    df_view["conversation"] = df_view["conversation"].apply(lambda cid: f"?call={cid}")
    df_view = df_view.drop(columns=["_cid"])

    edited = st.data_editor(
        df_view, hide_index=True, width="stretch",
        column_order=["select", "conversation", "direction", "started_at", "duration", "turns", "eval", "score"],
        column_config={
            "select": st.column_config.CheckboxColumn("✓", help="Select for batch validation"),
            "conversation": st.column_config.LinkColumn(
                "Conversation", width="medium", help="Click to open the call",
                display_text=r"\?call=(.+)"),
            "score": st.column_config.NumberColumn("Score", help="Overall 0-100"),
        },
        disabled=["conversation", "direction", "started_at", "duration", "turns", "eval", "score"],
        key="calls_table",
    )

    selected = df.loc[edited["select"].values, "_cid"].tolist()
    cA, _ = st.columns([1, 3])
    with cA:
        if st.button(f"▶ Validate selected ({len(selected)})", disabled=not selected, type="primary"):
            run_validation(selected)
            st.rerun()


# ------------------------------------------------------------------ UI: detail

def page_detail(conversation_id: str):
    if st.button("← Back to all calls"):
        st.query_params.clear()
        st.rerun()

    st.title(f"Call {conversation_id}")
    colv, _ = st.columns([1, 3])
    with colv:
        if st.button("🔁 Validate / re-validate", type="primary"):
            run_validation([conversation_id])
            st.rerun()

    e = store.get_evaluation(conversation_id)
    if not e:
        st.info("This call hasn't been validated yet. Click **Validate** above.")
    else:
        ok = e["overall_passed"]
        st.subheader(f"{'✅ PASS' if ok else '❌ FAIL'}  ·  {e['overall_score']}/100  ·  judge: {e['judge_model']}  ·  {e['validated_at']}")
        cols = st.columns(4)
        labels = {"conversation": "Conversation Quality", "tool": "Tool Correctness",
                  "business": "Business Outcome", "voice": "Voice Quality"}
        for col, d in zip(cols, DIMENSIONS):
            passed = (e.get("dim_passed") or {}).get(d, False)
            col.metric(labels[d], f"{e['dim_'+d]}/100", "PASS" if passed else "FAIL",
                       delta_color="normal" if passed else "inverse")

        for d in DIMENSIONS:
            with st.expander(f"{labels[d]} — checks", expanded=not (e.get('dim_passed') or {}).get(d, False)):
                for r in [r for r in e["results"] if r["dimension"] == d]:
                    if r["na"]:
                        icon, extra = "➖", " (n/a)"
                    elif r["passed"]:
                        icon, extra = "✅", (f" — {r['score']}/5" if r["score"] is not None else "")
                    else:
                        icon, extra = "❌", (f" — {r['score']}/5" if r["score"] is not None else "")
                    crit = " · **critical**" if r["critical"] else ""
                    st.markdown(f"{icon} **{r['name']}**{extra}{crit}  \n&nbsp;&nbsp;&nbsp;&nbsp;_{r['reasoning']}_")

    st.divider()
    # transcript + tool calls (from the eval snapshot if present, else fetch live)
    transcript = (e or {}).get("transcript_snapshot")
    tool_calls = (e or {}).get("tool_calls_snapshot")
    summary = (e or {}).get("post_call_summary")
    if not transcript:
        try:
            detail = el.get_conversation(conversation_id)
            transcript = el.extract_transcript(detail)
            tool_calls = el.extract_tool_calls(detail)
            summary = el.post_call_summary(detail)
        except Exception as ex:
            st.error(f"Could not load transcript: {ex}")
            transcript, tool_calls, summary = [], [], None

    if summary:
        st.markdown("**Post-call summary:** " + summary)

    st.markdown("**Transcript**")
    for t in transcript:
        who = "🧑 Customer" if t["role"] in ("user", "customer") else "💎 Ria" if t["role"] in ("agent", "assistant") else t["role"]
        if t["message"]:
            st.markdown(f"**{who}:** {t['message']}")
        for tc in t.get("tool_calls", []):
            nm = tc.get("tool_name") or tc.get("requested_tool_name") or "tool"
            st.caption(f"🔧 called `{nm}`")
        for tr in t.get("tool_results", []):
            nm = tr.get("tool_name") or "tool"
            st.caption(f"↩️ `{nm}` returned{' (error)' if tr.get('is_error') else ''}")

    if tool_calls:
        with st.expander("All tool calls"):
            st.json(tool_calls)


# ------------------------------------------------------------------------- main

def main():
    missing = [k for k in ("ELEVENLABS_API_KEY", "DATABASE_URL") if not os.environ.get(k)]
    if missing:
        st.error(f"Missing env vars: {', '.join(missing)}")
        st.stop()
    if not os.environ.get("GEMINI_API_KEY"):
        st.warning("GEMINI_API_KEY not set — validation will fail until it's configured.")

    apply_theme()
    outbound_call_panel()

    call_id = st.query_params.get("call")
    if call_id:
        page_detail(call_id)
    else:
        page_list()


main()
