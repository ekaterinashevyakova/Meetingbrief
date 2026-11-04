# Frontend integration

## Current implementation

- app.py: Streamlit form and briefing display.
- demo_data.py: fictional example response.
- No live research or model calls are performed.

## Integration point

The interface calls:

    result = generate_briefing(request)

The function currently lives in demo_data.py.

To connect the backend, implement the function in a backend module
and update the import in app.py. The function must return a Python
dictionary in the format below, not a JSON string.

## Request

Example:

    {
        "entity_type": "company",
        "name": "Example Company",
        "details": "https://example.com",
        "meeting_datetime": "2026-11-06T14:00:00+01:00",
        "timezone": "Europe/Rome",
        "goal": "Discuss a potential partnership"
    }

- entity_type: "company" or "person".
- name: required, trimmed text.
- details: optional context; an empty string if omitted.
- meeting_datetime: ISO 8601 timestamp with a UTC offset.
- timezone: IANA time zone name.
- goal: optional text; an empty string if omitted.

The backend is responsible for calculating the research window
and recording when research was performed.

## Response

All fields below are required. Lists may be empty.

    {
        "is_demo": false,
        "summary": "A concise summary of the findings.",
        "events": [
            {
                "title": "Event title",
                "date": "2026-11-02",
                "description": "A source-backed description.",
                "source_ids": ["S1"],
                "discussion_point": "A suggested topic to discuss."
            }
        ],
        "questions": ["A useful meeting question?"],
        "conversation_starters": ["A suggested opening sentence."],
        "gaps": ["A relevant limitation or uncertainty."],
        "sources": [
            {
                "id": "S1",
                "title": "Source title",
                "url": "https://example.com",
                "excerpt": "The supporting passage from the source."
            }
        ]
    }

The values above demonstrate structure only and are not real findings.

## Field rules

- is_demo must be true for fictional or demonstration content.
- Set is_demo to false only for a real research response.
- Source IDs must be unique strings.
- Event source_ids must refer to entries in sources.
- Event dates use YYYY-MM-DD, or null if unknown.
- Source URLs use HTTP or HTTPS, or null if unavailable.
- Questions, conversation starters and gaps are lists of strings.
- Content shown in the interface should be in English.

For Python dictionaries, use True, False and None.
For JSON, use true, false and null.

## Empty results

Return empty lists and an explanatory summary when research
completes successfully but finds insufficient relevant information.
Describe the limitation in gaps.

## Failures

Raise an exception when the workflow cannot complete.
The interface displays a generic error and retains the previous
successful result.

Do not return invented findings to hide a search or model failure.

## Validation boundaries

The interface checks the basic response structure and limits
source links to HTTP and HTTPS.

These checks do not verify factual accuracy.
Source relevance, evidence support, entity identity and date
validation remain responsibilities of the research workflow.

## Testing without the backend

Use fictional demo_data.py content.
Do not add real API keys to frontend files.