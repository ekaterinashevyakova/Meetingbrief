# Frontend manual checks

These checks cover the Streamlit interface with demonstration data.
They do not validate live research or factual accuracy.

## Passed checks

- Submitting an empty name displays a warning.
- Company / Person selection changes field labels.
- Submitted meeting details appear in the briefing.
- The previous briefing remains when switching entity type.
- Clear briefing removes the displayed result.
- A structured demo response displays events, questions and sources.
- Source details expand to show the supporting excerpt.
- An empty response displays appropriate messages without old events.
- A simulated generation failure displays an error message.
- The normal demo response works after restoring demo_data.py.

## Checks still required

- Verify clickable source links with real research results.
- Verify the interface on a narrow browser window.
- Run setup instructions on another team member's computer.
- Test the full Tavily and Bifrost integration.
- Review real claims, citations, entity relevance and dates.