from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import streamlit as st
import logging

from briefing_generator import generate_briefing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


st.set_page_config(page_title="MeetingBrief", page_icon="📋")


def safe_url(value):
    """Allow only HTTP and HTTPS source links."""
    if not isinstance(value, str):
        return None

    try:
        parsed = urlparse(value)
        if parsed.scheme in ("https", "http") and parsed.hostname:
            return value
    except ValueError:
        pass

    return None


def render_briefing(request, result):
    """Display a briefing independently of how it was generated."""
    st.divider()
    st.subheader(f"Meeting briefing: {request['name']}")

    st.caption(
        "Last generated briefing. Form edits apply after "
        "you click Generate briefing."
    )

    if result.get("is_demo", True):
        st.warning(
            "Fictional demo content. These findings do not describe "
            "the company or person you entered."
        )

    meeting = datetime.fromisoformat(request["meeting_datetime"])
    st.write(
        f"Meeting: {meeting:%d %b %Y, %H:%M} · "
        f"{request['timezone']}"
    )
    st.write(f"Meeting with: {request['entity_type'].title()}")

    if request["details"]:
        st.write(f"Additional context: {request['details']}")

    if request["goal"]:
        st.write(f"Objective: {request['goal']}")

    st.markdown("### At a glance")
    st.write(result.get("summary") or "No summary available.")

    sources = result.get("sources") or []
    source_map = {source["id"]: source for source in sources}

    st.markdown("### Recent developments")
    events = result.get("events") or []

    if not events:
        st.info(
            "No recent developments were returned. "
            "This does not mean that no events occurred."
        )

    for event in events:
        with st.container(border=True):
            st.subheader(event.get("title") or "Untitled development")

            st.caption(
                f"Event date: {event.get('date') or 'Not established'}"
            )

            st.write(event.get("description") or "No description available.")

            source_ids = event.get("source_ids") or []

            if not source_ids:
                st.caption("No supporting source provided.")

            for source_id in source_ids:
                source = source_map.get(source_id)

                if source is None:
                    st.caption(f"{source_id}: source unavailable.")
                    continue

                label = f"{source_id} — {source['title']}"
                url = safe_url(source.get("url"))

                if url:
                    st.link_button(label, url)
                else:
                    st.caption(f"{label} · No source link available")

            if event.get("discussion_point"):
                st.write(
                    f"Suggested discussion point: "
                    f"{event['discussion_point']}"
                )

    sections = [
        ("Questions to ask", "questions"),
        ("Conversation starters", "conversation_starters"),
        ("Information gaps", "gaps"),
    ]

    for heading, key in sections:
        st.markdown(f"### {heading}")
        items = result.get(key) or []

        if not items:
            st.write("No items provided.")

        for index, item in enumerate(items, start=1):
            st.write(f"{index}. {item}")

    st.markdown("### Sources")

    if not sources:
        st.write("No sources retrieved.")

    for source in sources:
        label = f"{source['id']} — {source['title']}"

        with st.expander(label):
            url = safe_url(source.get("url"))

            if url:
                st.link_button("Open source", url)
            else:
                st.write("No source link available.")

            st.write("Supporting excerpt:")
            st.write(source.get("excerpt") or "No excerpt provided.")

    if st.button("Clear briefing"):
        st.session_state.briefing = None
        st.rerun()


st.title("MeetingBrief")
st.write("Prepare for your meeting with a concise, source-backed briefing.")

if "briefing" not in st.session_state:
    st.session_state.briefing = None

entity_type = st.radio(
    "Who are you meeting?",
    ["Company", "Person"],
    horizontal=True,
)

is_company = entity_type == "Company"

with st.form(f"meeting_form_{entity_type}"):
    name = st.text_input(
        "Company name" if is_company else "Full name",
        placeholder="e.g. IKEA" if is_company else "e.g. Jane Smith",
    )

    details = st.text_input(
        "Company website (optional)"
        if is_company
        else "Company or role (optional)",
        placeholder="e.g. https://www.ikea.com"
        if is_company
        else "e.g. Marketing Director at Example Company",
    )

    date_column, time_column = st.columns(2)

    with date_column:
        meeting_date = st.date_input(
            "Meeting date",
            format="DD/MM/YYYY",
        )

    with time_column:
        meeting_time = st.time_input("Meeting time")

    timezone = st.selectbox(
        "Time zone",
        ["Europe/Rome", "Europe/Moscow", "UTC"],
    )

    goal = st.text_area(
        "Meeting objective (optional)",
        placeholder="e.g. Discuss a potential partnership",
    )

    submitted = st.form_submit_button(
        "Generate briefing",
        type="primary",
    )

if submitted:
    if not name.strip():
        field_name = "a company name" if is_company else "a person's name"
        st.warning(f"Please enter {field_name}.")
    else:
        meeting_datetime = datetime.combine(
            meeting_date,
            meeting_time,
            tzinfo=ZoneInfo(timezone),
        )

        request = {
            "entity_type": entity_type.lower(),
            "name": name.strip(),
            "details": details.strip(),
            "meeting_datetime": meeting_datetime.isoformat(),
            "timezone": timezone,
            "goal": goal.strip(),
        }

        try:
            with st.spinner("Preparing your briefing..."):
                result = generate_briefing(request)

                if not isinstance(result, dict):
                    raise ValueError("Expected a briefing dictionary.")

                if not isinstance(result.get("summary"), str):
                    raise ValueError("Expected a summary string.")

                for key in (
                    "events",
                    "questions",
                    "conversation_starters",
                    "gaps",
                    "sources",
                ):
                    if not isinstance(result.get(key), list):
                        raise ValueError(f"Expected a list for {key}.")

                for source in result["sources"]:
                    if not isinstance(source, dict):
                        raise ValueError("Invalid source.")
                    if not isinstance(source.get("id"), str):
                        raise ValueError("Invalid source ID.")
                    if not isinstance(source.get("title"), str):
                        raise ValueError("Invalid source title.")

                for event in result["events"]:
                    if not isinstance(event, dict):
                        raise ValueError("Invalid event.")
                    if not isinstance(event.get("source_ids", []), list):
                        raise ValueError("Invalid source references.")

            st.session_state.briefing = {
                "request": request,
                "result": result,
            }

        except Exception as error:
            logger.exception("Failed to generate briefing")

            st.error(
                "We couldn't prepare this briefing. Please try again. "
                "If a previous briefing appears below, it has not been updated."
            )

            with st.expander("Technical error details"):
                st.exception(error)

if st.session_state.briefing is not None:
    saved = st.session_state.briefing
    render_briefing(saved["request"], saved["result"])
