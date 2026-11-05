import os
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tavily import TavilyClient


def _parse_published_date(value, local_timezone):
    """Разбирает дату Tavily; она может относиться к публикации или обновлению."""
    if not value:
        return None, None

    if isinstance(value, datetime):
        parsed = value
    else:
        raw_value = str(value).strip()

        try:
            parsed = datetime.fromisoformat(
                raw_value.replace("Z", "+00:00")
            )
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw_value)
            except (TypeError, ValueError, OverflowError):
                try:
                    return None, date.fromisoformat(raw_value[:10])
                except ValueError:
                    return None, None

    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        local_value = parsed.astimezone(local_timezone)
        return local_value, local_value.date()

    # Если время пришло без часового пояса, сравнивать его точно нельзя.
    return None, parsed.date()


def search_sources(request):
    """Ищет источники в пределах 30 дней до встречи."""

    name = str(request.get("name", "")).strip()
    if not name:
        raise ValueError("The entity name is required.")

    meeting_value = request.get("meeting_datetime")
    if not meeting_value:
        raise ValueError("The meeting date and time are required.")

    timezone_name = str(request.get("timezone", "")).strip()
    if not timezone_name:
        raise ValueError("The meeting timezone is required.")

    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError("The meeting timezone is not a valid IANA timezone.") from error

    meeting_datetime = datetime.fromisoformat(
        str(meeting_value).replace("Z", "+00:00")
    )
    if meeting_datetime.tzinfo is None or meeting_datetime.utcoffset() is None:
        raise ValueError("The meeting time must include a UTC offset.")

    meeting_datetime = meeting_datetime.astimezone(local_timezone)
    collected_at = datetime.now(local_timezone)

    # Окно отсчитываем от встречи, а поиск заканчиваем не позже встречи
    # и не позже фактического времени сбора информации.
    window_start = meeting_datetime - timedelta(days=30)
    search_cutoff = min(meeting_datetime, collected_at)

    research_performed_at = datetime.now(timezone.utc).isoformat()

    # Если все 30 дней до встречи ещё впереди, Tavily не вызываем.
    if window_start > collected_at:
        return {
            "research_performed_at": research_performed_at,
            "research_window": {
                "start_date": window_start.date().isoformat(),
                "end_date": meeting_datetime.date().isoformat(),
            },
            "sources": [],
            "gaps": [
                "The full 30-day research window is in the future. "
                "No search was performed."
            ],
        }

    entity_type = str(request.get("entity_type", "company")).strip().lower()
    details = str(request.get("details", "")).strip()

    query = f'"{name}" {entity_type} recent news developments'
    if details:
        query += f" {details}"

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is not configured.")

    client = TavilyClient(api_key=api_key)

    # Берём небольшой запас по календарным дням: точную границу затем
    # проверяем по published_date в Python.
    response = client.search(
        query=query,
        topic="news",
        start_date=(window_start.date() - timedelta(days=1)).isoformat(),
        end_date=(search_cutoff.date() + timedelta(days=1)).isoformat(),
        max_results=10,
        include_answer=False,
        include_published_date=True,
        include_raw_content=True,
    )

    sources = []
    seen_urls = set()
    gaps = []
    missing_date_count = 0
    ambiguous_date_count = 0

    meeting_has_passed = meeting_datetime <= collected_at

    for item in response.get("results", []):
        raw_published_date = item.get("published_date")
        published_at, published_day = _parse_published_date(
            raw_published_date,
            local_timezone,
        )

        if published_day is None:
            missing_date_count += 1
            continue

        if published_at is not None:
            # Tavily вернул время, но оно может относиться к обновлению страницы.
# Используем его только для консервативного временного фильтра.
            if published_at < window_start or published_at > search_cutoff:
                continue
        else:
            # Есть только день. Проверяем, что он попадает в окно.
            if not window_start.date() <= published_day <= search_cutoff.date():
                continue

            # На граничный день без точного времени нельзя надёжно
            # проверить, не вышла ли статья до начала окна или после встречи.
            starts_midday_or_later = (
                published_day == window_start.date()
                and window_start.time() != time.min
            )
            same_day_as_past_meeting = (
                meeting_has_passed
                and published_day == search_cutoff.date()
            )

            if starts_midday_or_later or same_day_as_past_meeting:
                ambiguous_date_count += 1
                continue

        url = str(item.get("url", "")).strip()
        parts = urlsplit(url)

        if parts.scheme not in {"http", "https"} or not parts.netloc:
            continue

        clean_url = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path.rstrip("/"),
                parts.query,
                "",
            )
        )

        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        excerpt = str(item.get("content", "")).strip()
        article_text = str(item.get("raw_content", "")).strip()

        if not excerpt and article_text:
            excerpt = article_text[:1000]

        if not excerpt:
            continue

        normalized_published_date = (
            published_at.isoformat()
            if published_at is not None
            else published_day.isoformat()
        )

        sources.append(
            {
                "id": f"S{len(sources) + 1}",
                "title": str(item.get("title", "")).strip() or "Untitled source",
                "url": clean_url,
                "excerpt": excerpt,
                "published_date_note": (
    "Tavily date estimate; may represent original publication or a later "
    "update. Do not use it alone to infer relative event dates such as 'today'."
),
                # Текст статьи нужен модели для проверки даты события
                # и соответствия материала выбранной организации.
                "article_text": article_text or excerpt,
            }
        )

    if missing_date_count:
        gaps.append(
            "Some search results had no detectable publication date "
            "and were excluded."
        )

    if ambiguous_date_count:
        gaps.append(
            "Some results had only a calendar date on a boundary day. "
            "Their timing could not be verified, so they were excluded."
        )

    if not sources:
        gaps.append(
            "The search found no usable sources for this entity and date range. "
            "This does not prove that no relevant events occurred."
        )

    return {
        "research_performed_at": datetime.now(timezone.utc).isoformat(),
        "research_window": {
            "start_date": window_start.date().isoformat(),
            "end_date": search_cutoff.date().isoformat(),
        },
        "sources": sources,
        "gaps": gaps,
    }