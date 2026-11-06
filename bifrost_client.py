import os
import logging

from openai import OpenAI
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"

load_dotenv(ENV_PATH, override=True)

logger = logging.getLogger(__name__)

print("ENV file exists:", ENV_PATH.exists())
print("ENV file path:", ENV_PATH)
print("BIFROST_BASIC_AUTH loaded:", bool(os.getenv("BIFROST_BASIC_AUTH")))


def get_bifrost_client():
    base_url = os.getenv("BIFROST_BASE_URL")
    basic_auth = os.getenv("BIFROST_BASIC_AUTH")

    if not base_url:
        raise RuntimeError("BIFROST_BASE_URL is not configured.")

    if not basic_auth:
        raise RuntimeError("BIFROST_BASIC_AUTH is not configured.")

    return OpenAI(
        base_url=base_url,
        api_key="not-needed",
        default_headers={
            "Authorization": basic_auth
        },
        timeout=120.0
    )



def generate_text(messages):
    model = os.getenv("BIFROST_MODEL")

    if not model:
        raise RuntimeError("BIFROST_MODEL is not configured.")

    logger.info("Sending request to Bifrost. Model: %s", model)

    client = get_bifrost_client()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_completion_tokens=4096,
            reasoning_effort="low",
            extra_body={
                "include_reasoning": False
            }
        )

        choice = response.choices[0]
        message = choice.message

        logger.info("Finish reason: %s", choice.finish_reason)
        logger.info("Response content length: %s", len(message.content or ""))

        logger.info(
            "Reasoning field exists: %s",
            bool(getattr(message, "reasoning", None))
        )

        logger.info("Usage: %s", response.usage)

        if not response.choices:
            raise RuntimeError("Bifrost returned no choices.")

        content = response.choices[0].message.content

        if not content or not content.strip():
            raise RuntimeError("Bifrost returned an empty response.")

        logger.info(
            "Bifrost response received. Length: %d characters",
            len(content)
        )

        return content

    except Exception:
        logger.exception("Bifrost request failed")
        raise
