import json
import re
from datetime import date

from tavily_search import search_sources
from bifrost_client import generate_text

import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an experienced business research analyst.

Your task is to prepare a concise, accurate and useful briefing
for an upcoming business meeting.

You will receive information about an organization or person,
the meeting objective, and a collection of news articles.

IMPORTANT RULES:

1. Use ONLY the information provided in the sources.
2. Do not invent events, facts, dates, or conclusions.
3. Analyze the full article text whenever available.
4. Distinguish between the publication date and the actual
   date of an event.
5. The Tavily publication date is only an estimate.
   It may refer to the original publication or a later update.
6. Never use the Tavily publication date alone to determine
   when an event happened.
7. If the event date cannot be established from the article,
   return null.
8. Do not confuse events involving similarly named organizations.
9. Every event must contain at least one supporting source ID.
10. Source IDs must exactly match the IDs provided in the input.
11. Do not create hypothetical events.
12. If there is insufficient information, state this explicitly.

The output must be valid JSON.
Do not use Markdown or code blocks.

Return the following structure:

{
    "summary": "General overview of the available information",
    "events": [
        {
            "title": "Event title",
            "date": "YYYY-MM-DD or null",
            "description": "Description of the event",
            "source_ids": ["S1"],
            "discussion_point": "Why this event matters for the meeting"
        }
    ],
    "questions": [
        "Questions worth asking during the meeting"
    ],
    "conversation_starters": [
        "Suggested conversation starters"
    ],
    "gaps": [
        "Missing information or limitations"
    ]
}

Additional requirements:

- Events should be ordered from newest to oldest when their
  dates are known.
- Prioritize developments relevant to the meeting objective.
- Avoid repeating the same event in different formulations.
- Questions should be specific and useful.
- Conversation starters should sound natural.
- If no relevant developments are found, return an empty
  events array and explain the lack of information in summary.
"""


def _extract_json(text):
    """Extract JSON from the model response."""

    text = text.strip()

    # Remove Markdown code block if present.
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON if the model added text around it.
        decoder = json.JSONDecoder()

        start = text.find("{")

        if start == -1:
            raise ValueError("No JSON object found in model response.")

        result, _ = decoder.raw_decode(text[start:])

        return result


def _validate_result(result, valid_source_ids):
    """Validate the structure and source references."""

    if not isinstance(result, dict):
        raise ValueError("Model response must be a JSON object.")

    required_fields = [
        "summary",
        "events",
        "questions",
        "conversation_starters",
        "gaps",
    ]

    for field in required_fields:
        if field not in result:
            raise ValueError(f"Missing field: {field}")

    if not isinstance(result["summary"], str):
        raise ValueError("Summary must be a string.")

    for field in (
        "events",
        "questions",
        "conversation_starters",
        "gaps",
    ):
        if not isinstance(result[field], list):
            raise ValueError(f"{field} must be a list.")

    for event in result["events"]:

        if not isinstance(event, dict):
            raise ValueError("Invalid event object.")

        for field in (
            "title",
            "description",
            "source_ids",
            "discussion_point",
        ):
            if field not in event:
                raise ValueError(f"Event is missing {field}.")

        if not isinstance(event["source_ids"], list):
            raise ValueError("Event source_ids must be a list.")

        if not event["source_ids"]:
            raise ValueError(
                "Every event must have at least one source."
            )

        for source_id in event["source_ids"]:
            if source_id not in valid_source_ids:
                raise ValueError(
                    f"Unknown source ID: {source_id}"
                )

        event_date = event.get("date")

        if event_date is not None:
            try:
                date.fromisoformat(event_date)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Invalid event date: {event_date}"
                )

    return result


def _prepare_sources_context(sources):
    """Prepare source information for the LLM."""

    prepared = []

    for source in sources:

        prepared.append({
            "id": source["id"],
            "title": source["title"],
            "url": source["url"],
            "excerpt": source["excerpt"],
            "published_date": source["published_date"],
            "published_date_note": source["published_date_note"],
            "article_text": source["article_text"][:1500],
        })

    return prepared


def generate_briefing(request):
    """
    Generate a meeting briefing using Tavily and Bifrost.
    """

    # 1. Retrieve sources from Tavily.
    logger.info("Starting briefing generation for: %s", request["name"])

    research = search_sources(request)

    sources = research["sources"]

    logger.info("Tavily returned %d usable sources", len(sources))

    # If no sources were found, do not ask the LLM to invent information.
    if not sources:

        return {
            "is_demo": False,
            "summary": (
                "No usable sources were found for the selected "
                "organization and research period. "
                "A reliable briefing cannot be prepared."
            ),
            "events": [],
            "questions": [],
            "conversation_starters": [],
            "gaps": research["gaps"],
            "sources": [],
        }

    # 2. Prepare context for the model.
    logger.info("Preparing prompt for Grok")

    sources_context = _prepare_sources_context(sources)

    user_context = {
        "meeting": {
            "entity_type": request["entity_type"],
            "name": request["name"],
            "details": request.get("details", ""),
            "meeting_datetime": request["meeting_datetime"],
            "timezone": request["timezone"],
            "goal": request.get("goal", ""),
        },
        "research": {
            "performed_at": research["research_performed_at"],
            "window": research["research_window"],
        },
        "sources": sources_context,
    }

    user_prompt = f"""
Prepare a briefing using the following information.

MEETING AND RESEARCH CONTEXT:

{json.dumps(user_context, ensure_ascii=False, indent=2)}

Remember:
- Analyze the supplied article texts.
- Identify actual events, not merely article publication dates.
- Use only the supplied source IDs.
- Return valid JSON following the required structure.
"""

    # 3. Send request to Grok through Bifrost.
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]

    response_text = generate_text(messages)
    logger.info("Grok response received")

    # 4. Parse model response.
    result = _extract_json(response_text)
    logger.info("JSON successfully parsed")

    # 5. Validate generated information.
    valid_source_ids = {
        source["id"] for source in sources
    }

    result = _validate_result(
        result,
        valid_source_ids
    )
    logger.info(
        "Briefing validated: %d events, %d questions",
        len(result["events"]),
        len(result["questions"])
    )

    # 6. Combine model output with original Tavily sources.
    result["is_demo"] = False

    result["sources"] = sources

    # Preserve search limitations.
    result["gaps"].extend(research["gaps"])

    return result
