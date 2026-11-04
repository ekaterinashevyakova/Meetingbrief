import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from tavily import TavilyClient


def search_sources(request):
    """Ищет реальные веб-источники и возвращает их для дальнейшей обработки."""

    name = str(request.get("name", "")).strip()
    if not name:
        raise ValueError("The entity name is required.")

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is not configured.")

    entity_type = str(request.get("entity_type", "company")).strip().lower()
    details = str(request.get("details", "")).strip()

    meeting_value = request.get("meeting_datetime")
    if meeting_value:
        meeting_date = datetime.fromisoformat(
            meeting_value.replace("Z", "+00:00")
        ).date()
    else:
        meeting_date = date.today()

    # Не ищем события из будущего, даже если встреча назначена на будущее.
    end_date = min(meeting_date, date.today())
    start_date = end_date - timedelta(days=90)

    query = f'"{name}" {entity_type} recent news developments'
    if details:
        query += f" {details}"

    client = TavilyClient(api_key=api_key)
    response = client.search(
        query=query,
        topic="news",
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        max_results=10,
        include_answer=False,
        include_raw_content=False,
    )

    sources = []
    seen_urls = set()

    for item in response.get("results", []):
        url = str(item.get("url", "")).strip()
        parts = urlsplit(url)

        if parts.scheme not in {"http", "https"} or not parts.netloc:
            continue

        # Убираем фрагмент ссылки и завершающий слэш для проверки дублей.
        clean_url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/"), parts.query, "")
        )
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        excerpt = str(item.get("content", "")).strip()
        if not excerpt:
            continue

        source = {
            "id": f"S{len(sources) + 1}",
            "title": str(item.get("title", "")).strip() or "Untitled source",
            "url": clean_url,
            "excerpt": excerpt,
            "published_date": item.get("published_date"),
        }
        sources.append(source)

    gaps = []
    if not sources:
        gaps.append(
            "The search found no usable sources for this entity and date range. "
            "This does not prove that no relevant events occurred."
        )

    return {
        "research_performed_at": datetime.now(timezone.utc).isoformat(),
        "research_window": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "sources": sources,
        "gaps": gaps,
    }