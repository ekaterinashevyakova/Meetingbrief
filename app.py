import streamlit as st

st.set_page_config(page_title="MeetingBrief", page_icon="📋")

st.title("MeetingBrief")
st.write("Prepare for your meeting with a concise, source-backed briefing.")
st.info("Demo mode. Web research and AI are not connected yet.")

with st.form("meeting_form"):
    entity_type = st.radio(
        "Who are you meeting?",
        ["Company", "Person"],
    )

    name = st.text_input("Company name or person's name")

    details = st.text_input(
        "Company website or person's role (optional)"
    )

    meeting_date = st.date_input("Meeting date")
    meeting_time = st.time_input("Meeting time")

    timezone = st.selectbox(
        "Time zone",
        ["Europe/Rome", "Europe/Moscow", "UTC"],
    )

    goal = st.text_area("Meeting objective (optional)")

    submitted = st.form_submit_button("Generate briefing")

if submitted:
    if not name.strip():
        st.warning("Please enter a company name or person's name.")
    else:
        st.subheader(f"Meeting briefing: {name.strip()}")

        st.write(
            f"Meeting: {meeting_date:%d %b %Y}, "
            f"{meeting_time:%H:%M} · {timezone}"
        )

        if goal.strip():
            st.write(f"Objective: {goal.strip()}")

        st.caption(
            "Preview only. The sections below contain placeholders, "
            "not research findings."
        )

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