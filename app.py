from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import streamlit as st

from demo_data import generate_briefing


st.set_page_config(
    page_title="MeetingBrief",
    page_icon="📋",
    layout="wide",
)


def safe_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
        if parsed.scheme in ("https", "http") and parsed.hostname:
            return value
    except ValueError:
        pass
    return None


def validate_result(result):
    if not isinstance(result, dict):
        raise ValueError("Expected a dictionary.")

    if not isinstance(result.get("summary"), str):
        raise ValueError("Expected a summary.")

    if not isinstance(result.get("is_demo"), bool):
        raise ValueError("Expected a demo flag.")

    for key in (
        "events", "questions", "conversation_starters", "gaps", "sources"
    ):
        if not isinstance(result.get(key), list):
            raise ValueError(f"Expected a list: {key}")

    source_ids = set()
    for source in result["sources"]:
        if not isinstance(source, dict):
            raise ValueError("Invalid source.")
        if not isinstance(source.get("id"), str):
            raise ValueError("Invalid source ID.")
        if source["id"] in source_ids:
            raise ValueError("Duplicate source ID.")
        source_ids.add(source["id"])
        if not isinstance(source.get("title"), str):
            raise ValueError("Invalid source title.")

    for event in result["events"]:
        if not isinstance(event, dict):
            raise ValueError("Invalid event.")
        for key in ("title", "description"):
            if not isinstance(event.get(key), str):
                raise ValueError(f"Invalid event {key}.")
        references = event.get("source_ids", [])
        if not isinstance(references, list):
            raise ValueError("Invalid source references.")
        if any(
            not isinstance(ref, str) or ref not in source_ids
            for ref in references
        ):
            raise ValueError("Unknown source reference.")

    for key in ("questions", "conversation_starters", "gaps"):
        if any(not isinstance(item, str) for item in result[key]):
            raise ValueError(f"Invalid text in {key}.")

def research_context(request, result):
    meeting = datetime.fromisoformat(request["meeting_datetime"])
    window_start = meeting - timedelta(days=30)

    lines = [
        (
            "Requested research window: "
            f"{window_start:%d %b %Y, %H:%M} to "
            f"{meeting:%d %b %Y, %H:%M} "
            f"({request['timezone']})"
        )
    ]

    if result["is_demo"]:
        lines.append(
            "Research collected at: Not performed — fictional demo."
        )
        return lines

    collected_at = result.get("researched_at")

    if not collected_at:
        lines.append("Research collected at: Not provided.")
        return lines

    try:
        collected = datetime.fromisoformat(collected_at)

        if collected.tzinfo is None:
            raise ValueError("Research timestamp must include a time zone.")

        collected = collected.astimezone(ZoneInfo(request["timezone"]))

        lines.append(
            f"Research collected at: {collected:%d %b %Y, %H:%M} "
            f"({request['timezone']})"
        )

        if collected < meeting:
            lines.append(
                "Coverage note: The requested window extends beyond "
                "the collection time. Later developments are not covered."
            )

    except (TypeError, ValueError):
        lines.append(
            "Research collected at: Unavailable — invalid timestamp."
        )

    return lines

def export_briefing(request, result):
    meeting = datetime.fromisoformat(request["meeting_datetime"])

    lines = [
        f"# Meeting briefing: {request['name']}",
        "",
    ]

    if result["is_demo"]:
        lines.extend([
            "> FICTIONAL DEMO: this is not research about the named entity.",
            "",
        ])

    lines.extend([
        f"Meeting: {meeting:%d %b %Y, %H:%M} · {request['timezone']}",
        f"Meeting with: {request['entity_type'].title()}",
        f"Context: {request['details'] or 'Not provided'}",
        f"Objective: {request['goal'] or 'Not provided'}",
        *research_context(request, result),
        "",
        "## At a glance",
        result["summary"],
        "",
        "## Recent developments",
    ])

    if not result["events"]:
        lines.append("No recent developments returned.")

    for event in result["events"]:
        references = ", ".join(event.get("source_ids", []))
        lines.extend([
            "",
            f"### {event['title']}",
            f"Event date: {event.get('date') or 'Not established'}",
            event["description"],
            f"Source references: {references or 'None provided'}",
        ])
        if event.get("discussion_point"):
            lines.append(
                f"Suggested discussion: {event['discussion_point']}"
            )

    for heading, key in (
        ("Questions to ask", "questions"),
        ("Conversation starters", "conversation_starters"),
        ("Information gaps", "gaps"),
    ):
        lines.extend(["", f"## {heading}"])
        lines.extend(
            f"- {item}" for item in result[key]
        )
        if not result[key]:
            lines.append("No items provided.")

    lines.extend(["", "## Sources"])
    if not result["sources"]:
        lines.append("No sources retrieved.")

    for source in result["sources"]:
        lines.extend([
            "",
            f"### {source['id']} — {source['title']}",
            safe_url(source.get("url")) or "No source link available.",
            "Supporting excerpt:",
            source.get("excerpt") or "No excerpt provided.",
        ])

    return "\n\n".join(lines)


def render_briefing(request, result):
    meeting = datetime.fromisoformat(request["meeting_datetime"])

    st.subheader(f"Briefing: {request['name']}")
    st.caption(
        f"{meeting:%d %b %Y, %H:%M} · {request['timezone']} · "
        f"{request['entity_type'].title()}"
    )

    if result["is_demo"]:
        st.warning(
            "Fictional demo. These findings do not describe "
            "the company or person you entered."
        )

    if request["goal"]:
        st.write(f"**Meeting objective:** {request['goal']}")
    if request["details"]:
        st.write(f"**Context:** {request['details']}")

    for line in research_context(request, result):
        st.caption(line)    

    st.caption(
        "Showing the last generated briefing. "
        "Submit the form to apply changes."
    )

    st.markdown("#### At a glance")
    st.write(result["summary"] or "No summary available.")

    st.markdown("#### Recent developments")
    if not result["events"]:
        st.info(
            "No recent developments returned. "
            "This does not mean that no events occurred."
        )

    for index, event in enumerate(result["events"], start=1):
        with st.container(border=True):
            st.markdown(f"**{index}. {event['title']}**")
            st.caption(
                f"Event date: {event.get('date') or 'Not established'}"
            )
            st.write(event["description"])
            references = ", ".join(event.get("source_ids", []))
            st.caption(
                f"Source references: {references or 'None provided'}"
            )
            if event.get("discussion_point"):
                st.write(
                    f"**Suggested discussion:** "
                    f"{event['discussion_point']}"
                )

    for heading, key in (
        ("Questions to ask", "questions"),
        ("Conversation starters", "conversation_starters"),
        ("Information gaps", "gaps"),
    ):
        st.markdown(f"#### {heading}")
        if not result[key]:
            st.caption("No items provided.")
        for item in result[key]:
            st.markdown(f"- {item}")

    with st.expander(
        f"Sources and supporting excerpts ({len(result['sources'])})"
    ):
        if not result["sources"]:
            st.write("No sources retrieved.")

        for source in result["sources"]:
            st.markdown(f"**{source['id']} — {source['title']}**")
            url = safe_url(source.get("url"))
            if url:
                st.link_button(f"Open {source['id']}", url)
            else:
                st.caption("No source link available.")
            st.write(source.get("excerpt") or "No excerpt provided.")
            st.divider()

    st.download_button(
        "Download briefing (.md)",
        data=export_briefing(request, result),
        file_name="meetingbrief-demo.md"
        if result["is_demo"]
        else "meetingbrief.md",
        mime="text/markdown",
    )

    if st.button("Clear briefing"):
        st.session_state.briefing = None
        st.rerun()


st.title("MeetingBrief")
st.write("Walk into your next meeting informed and ready.")
st.caption(
    "Recent developments, useful questions and traceable sources "
    "in one concise briefing."
)
st.info("Demo mode · Live web research and AI are not connected yet.")

if "briefing" not in st.session_state:
    st.session_state.briefing = None

form_column, result_column = st.columns([2, 3], gap="large")

with form_column:
    st.subheader("Your meeting")

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
            placeholder="https://www.ikea.com"
            if is_company
            else "Marketing Director at Example Company",
        )

        meeting_date = st.date_input(
            "Meeting date",
            format="DD/MM/YYYY",
        )
        meeting_time = st.time_input("Meeting time")
        timezone = st.selectbox(
            "Time zone",
            ["Europe/Rome", "Europe/Moscow", "UTC"],
        )
        goal = st.text_area(
            "Meeting objective (optional)",
            placeholder="Discuss a potential partnership",
        )
        submitted = st.form_submit_button(
            "Generate briefing",
            type="primary",
        )

    if submitted:
        if not name.strip():
            st.warning("Please enter a company name or person's name.")
        else:
            meeting = datetime.combine(
                meeting_date,
                meeting_time,
                tzinfo=ZoneInfo(timezone),
            )
            request = {
                "entity_type": entity_type.lower(),
                "name": name.strip(),
                "details": details.strip(),
                "meeting_datetime": meeting.isoformat(),
                "timezone": timezone,
                "goal": goal.strip(),
            }

            try:
                with st.spinner("Preparing your briefing..."):
                    result = generate_briefing(request)
                    validate_result(result)

                st.session_state.briefing = {
                    "request": request,
                    "result": result,
                }
            except Exception:
                st.error(
                    "We couldn't prepare this briefing. Please try again. "
                    "Any previous briefing has not been updated."
                )

with result_column:
    saved = st.session_state.briefing
    if saved is None:
        st.subheader("Your briefing will appear here")
        st.write(
            "Enter meeting details on the left, "
            "then select Generate briefing."
        )
        with st.container(border=True):
            st.markdown("**What to expect**")
            st.write("Recent developments with source references")
            st.write("Questions and conversation starters")
            st.write("Information gaps and supporting excerpts")
        st.caption(
            "The current demo uses fictional examples. "
            "No research is performed."
        )
    else:
        render_briefing(saved["request"], saved["result"])