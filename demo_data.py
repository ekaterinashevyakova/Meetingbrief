def generate_briefing(request):
    """Return fictional demo data.

    Integration point: replace this function's implementation
    with the real research and generation workflow.
    """
    return {
        "is_demo": True,
        "summary": (
            "This fictional example demonstrates the briefing layout. "
            "It does not contain research about the requested company "
            "or person."
        ),
        "events": [
            {
                "title": "Example product announcement",
                "date": None,
                "description": (
                    "A fictional company announced a new collaboration "
                    "tool. This event is invented for interface testing."
                ),
                "source_ids": ["S1"],
                "discussion_point": (
                    "Ask which customer needs the tool is intended "
                    "to address."
                ),
            }
        ],
        "questions": [
            "What are your team's current priorities?",
            "What would make a partnership useful for your team?",
            "How would you measure the success of a pilot project?",
        ],
        "conversation_starters": [
            "I'd like to understand what your team is focusing on "
            "and where our work might overlap."
        ],
        "gaps": [
            "No live research has been performed.",
            "The example event is fictional and has no verified date.",
        ],
        "sources": [
            {
                "id": "S1",
                "title": "Fictional source — demonstration only",
                "url": None,
                "excerpt": (
                    "This is an invented supporting excerpt "
                    "used only to demonstrate the interface."
                ),
            }
        ],
    }