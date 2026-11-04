import streamlit as st

st.set_page_config(page_title="MeetingBrief", page_icon="📋")

st.title("MeetingBrief")
st.write("Prepare for your meeting with a concise, source-backed briefing.")
st.info("Demo mode. Web research and AI are not connected yet.")

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
        meeting_date = st.date_input("Meeting date")

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
        st.session_state.briefing = {
            "entity_type": entity_type,
            "name": name.strip(),
            "details": details.strip(),
            "meeting_date": meeting_date,
            "meeting_time": meeting_time,
            "timezone": timezone,
            "goal": goal.strip(),
        }

briefing = st.session_state.briefing

if briefing is not None:
    st.divider()
    st.subheader(f"Meeting briefing: {briefing['name']}")

    st.caption(
        "Last generated preview. Form edits apply when you click "
        "Generate briefing. All content below is demonstrative."
    )

    st.write(f"Meeting with: {briefing['entity_type']}")

    if briefing["details"]:
        st.write(f"Additional context: {briefing['details']}")

    st.write(
        f"Meeting: {briefing['meeting_date']:%d %b %Y}, "
        f"{briefing['meeting_time']:%H:%M} · {briefing['timezone']}"
    )

    if briefing["goal"]:
        st.write(f"Objective: {briefing['goal']}")

    st.markdown("### At a glance")
    st.write("A short summary of relevant findings will appear here.")

    st.markdown("### Recent developments")
    st.write(
        "Recent events will appear here with dates "
        "and references to supporting sources."
    )

    st.markdown("### Questions to ask")
    st.write(
        "General example question: "
        "What are your team's current priorities?"
    )

    st.markdown("### Conversation starters")
    st.write(
        "Suggestions tailored to the research "
        "and your meeting objective will appear here."
    )

    st.markdown("### Information gaps")
    st.write(
        "No research has been performed. "
        "This is an interface demonstration."
    )

    st.markdown("### Sources")
    st.write("No sources retrieved yet.")

    if st.button("Clear briefing"):
        st.session_state.briefing = None
        st.rerun()