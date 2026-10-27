"""
FarmTab AI assistant (showcase version).

One endpoint:  POST /sites/{site_id}/shelves/{shelf_id}/assistant/chat

The app sends a question (and optionally a photo) plus the earlier messages of the
conversation. This code adds what FarmTab knows about the shelf (the live sensor
reading, the threshold limits and the growing layers), asks the AI, and returns the
answer. The API key lives only on the server (an environment variable); the app
never sees it.

Which AI is used is chosen with AI_PROVIDER:
    gemini  (default)  Google Gemini, has a free tier   -> needs GEMINI_API_KEY
    claude             Anthropic Claude (paid)          -> needs ANTHROPIC_API_KEY

Only ask_ai() and the two small functions under it know about the providers, so
moving to another AI later (for example a local Ollama model) means adding one
more function.
"""

import base64
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image, ImageOps

from database.connection import get_connection
from shelves.shelves import (
    check_site_admin_or_staff,
    get_current_user_id,
)

logger = logging.getLogger("farmtab.assistant")

router = APIRouter(
    tags=["AI Assistant"],
)

# ------------------------------------------------------------
# Settings (can be changed with environment variables)
# ------------------------------------------------------------

PROVIDER = os.getenv("AI_PROVIDER", "gemini").strip().lower()

# The very newest model can be busy, so the default is a fast, proven Flash model
# (tested on this farm's key), with fallbacks that are tried automatically when Google answers
# "high demand" (503) or times out (504). The names must be models your key can
# use; list them with the test command from the setup notes. Model names change
# often: if you get "model not available", change these in backend/.env.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-3.6-flash"
    ).split(",")
    if m.strip()
]
GEMINI_CALL_TIMEOUT_MS = 40_000     # one attempt
GEMINI_TOTAL_SECONDS = 80           # give up after this long, over all attempts

# Only used when AI_PROVIDER=claude
CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

# Gemini counts its internal "thinking" in this limit too, so it is generous
MAX_OUTPUT_TOKENS = 2048

MAX_MESSAGE_CHARS = 4000        # one question
MAX_HISTORY_MESSAGES = 16       # earlier messages sent back with each question
MAX_HISTORY_CHARS = 6000        # one earlier message
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 1280           # photos are shrunk to this before sending (faster, smaller)

# A simple guard against a runaway app or script using up the free quota
RATE_LIMIT_CALLS = 30
RATE_LIMIT_WINDOW_SECONDS = 600

MYT = timezone(timedelta(hours=8))

_recent_calls = defaultdict(deque)



SYSTEM_PROMPT = """You are the FarmTab assistant, built into a farm-management app for an indoor hydroponic farm growing looseleaf lettuce under LED grow lights.

Your purpose is to answer general questions, explain lettuce-growing practices, help users understand farm sensor readings, and analyze uploaded plant photos.

How to answer:
- Be practical, clear and concise. Use short paragraphs and no more than 5 or 6 bullet points.
- Use simple language suitable for a beginner.
- Use plain text with simple bullets. Avoid Markdown tables and code blocks.
- The farm uses pH, EC in uS/cm, water temperature in degrees C, and ORP in mV.
- When discussing recommended lettuce conditions, provide typical ranges, explain that they depend on variety and growth stage, and compare them with the farm's configured limits when available.
- Distinguish general recommendations from this farm's configured limits.
- When discussing sensor readings, use the data and timestamps provided. If data is missing or may be outdated, state that clearly. Never invent readings.
- The farm uses a shared recirculating nutrient reservoir for its three growing levels. Do not imply that each level has a separate nutrient reading unless separate measurements are explicitly provided.

Image analysis:
- Describe only what is visible in the uploaded photo.
- Look for possible signs such as yellowing leaves, brown edges, spots, wilting, pests, algae or overcrowding.
- Distinguish visible observations from possible explanations. Do not claim a disease is confirmed from an image alone.
- Explain uncertainty and suggest useful next checks, such as pH, EC, water temperature, lighting or root condition.
- If the image is unclear, say so rather than guessing.
- For serious or persistent plant-health problems, recommend consulting a qualified agricultural specialist.

You can also answer general questions outside agriculture.
"""

# ============================================================
# SMALL HELPERS
# ============================================================

def check_rate_limit(user_id: int):
    now = time.time()
    calls = _recent_calls[user_id]

    while calls and now - calls[0] > RATE_LIMIT_WINDOW_SECONDS:
        calls.popleft()

    if len(calls) >= RATE_LIMIT_CALLS:
        raise HTTPException(
            status_code=429,
            detail="Too many questions in a short time. Please wait a few minutes.",
        )

    calls.append(now)


def parse_history(raw: str):
    """Turn the app's JSON history into a clean list of {'role', 'content'}."""
    try:
        items = json.loads(raw or "[]")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid conversation history.")

    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Invalid conversation history.")

    messages = []

    for item in items[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        text = str(item.get("text") or "").strip()

        if role not in ("user", "assistant") or not text:
            continue

        text = text[:MAX_HISTORY_CHARS]

        # The AI expects the roles to alternate, so join neighbours of the same role
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += "\n\n" + text
        else:
            messages.append({"role": role, "content": text})

    # The conversation must start with the user ...
    while messages and messages[0]["role"] != "user":
        messages.pop(0)

    # ... and must end with the assistant, because the new question follows
    while messages and messages[-1]["role"] == "user":
        messages.pop()

    return messages


def prepare_image(data: bytes):
    """Shrink a photo. Returns (media_type, JPEG bytes)."""
    try:
        with Image.open(BytesIO(data)) as original:
            image = ImageOps.exif_transpose(original)     # respect the phone's rotation
            image = image.convert("RGB")                  # JPEG has no transparency
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))

            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=85)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="That file could not be read as an image.",
        )

    return "image/jpeg", buffer.getvalue()


# ============================================================
# WHAT FARMTAB KNOWS ABOUT THIS SHELF
# ============================================================
# Each part is optional: if a query fails, the assistant still works,
# it just knows less about the farm.

def build_farm_context(connection, site_id: int, shelf_id: int) -> str:
    lines = []
    cursor = connection.cursor()

    # --- threshold limits set for this shelf
    try:
        cursor.execute(
            """
            SELECT ph_min, ph_max, ec_min, ec_max,
                   temperature_min, temperature_max, orp_min, orp_max
            FROM shelves
            WHERE id = %s AND site_id = %s
            """,
            (shelf_id, site_id),
        )
        t = cursor.fetchone()

        if t:
            lines.append(
                "Threshold limits set for this shelf: "
                f"pH {t[0]} to {t[1]}; EC {t[2]} to {t[3]} uS/cm; "
                f"temperature {t[4]} to {t[5]} C; ORP {t[6]} to {t[7]} mV."
            )
    except Exception:
        connection.rollback()
        logger.exception("assistant: could not read thresholds")

    # --- the latest sensor reading
    try:
        cursor.execute(
            """
            SELECT recorded_at, ph, ec, orp, temperature
            FROM sensor_readings
            WHERE shelf_id = %s
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            (shelf_id,),
        )
        r = cursor.fetchone()

        if r:
            when = r[0].astimezone(MYT).strftime("%d %b %H:%M") if r[0] else "?"
            lines.append(
                f"Latest sensor reading ({when}, Malaysia time): "
                f"pH {r[1]:.2f}; EC {r[2]:.0f} uS/cm; ORP {r[3]:.0f} mV; "
                f"temperature {r[4]:.1f} C."
            )
    except Exception:
        connection.rollback()
        logger.exception("assistant: could not read the sensor reading")

    # --- growing layers
    try:
        cursor.execute(
            """
            SELECT l.display_name, c.transplant_date, c.plant_count, d.canopy_cm2
            FROM layer_cycles c
            JOIN layers l
                ON l.id = c.layer_id
            JOIN shelves s
                ON s.id = l.shelf_id
            LEFT JOIN LATERAL (
                SELECT canopy_cm2
                FROM daily_cycle_records
                WHERE cycle_id = c.id
                  AND canopy_pixels IS NOT NULL
                ORDER BY plant_age_day DESC
                LIMIT 1
            ) d ON TRUE
            WHERE c.status = 'ACTIVE'
              AND l.shelf_id = %s
              AND s.site_id = %s
            ORDER BY l.sort_order
            """,
            (shelf_id, site_id),
        )
        rows = cursor.fetchall()

        if rows:
            today = datetime.now(MYT).date()
            parts = []

            for name, transplant, plants, cm2 in rows:
                day = (today - transplant).days + 1 if transplant else "?"
                canopy = f", canopy about {cm2:.0f} cm2" if cm2 is not None else ""
                parts.append(f"{name}: day {day}, {plants} plants{canopy}")

            lines.append(
                "Growing layers (looseleaf lettuce): " + "; ".join(parts) + ". "
                "Canopy figures are rough camera estimates and are not comparable between layers."
            )
    except Exception:
        connection.rollback()
        logger.exception("assistant: could not read the growing layers")

    cursor.close()

    if not lines:
        return ""

    return "\n\nLive data from this farm (use it when the question is about the farm):\n- " + "\n- ".join(lines)


# ============================================================
# THE AI PROVIDERS
# ============================================================
# Every provider function takes the same input and returns the same output:
#   earlier  = [{'role': 'user' | 'assistant', 'content': '...'}, ...]
#   question = the new question (text)
#   image    = None, or (media_type, JPEG bytes)
#   returns  = (answer text, input tokens, output tokens)

def ask_gemini(system_prompt: str, earlier: list, question: str, image):
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="The AI assistant is not set up on the server yet (GEMINI_API_KEY is missing).",
        )

    from google import genai
    from google.genai import errors, types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=GEMINI_CALL_TIMEOUT_MS),
    )

    # Gemini calls the assistant "model"
    contents = [
        types.Content(
            role="user" if m["role"] == "user" else "model",
            parts=[types.Part.from_text(text=m["content"])],
        )
        for m in earlier
    ]

    parts = []

    if image is not None:
        parts.append(types.Part.from_bytes(data=image[1], mime_type=image[0]))

    parts.append(types.Part.from_text(text=question))
    contents.append(types.Content(role="user", parts=parts))

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )

    # The first model, then the fallbacks. Google's free models are
    # sometimes busy, so a failure is not the end: retry once, then move on.
    candidates = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]

    started = time.time()
    last_code = None
    response = None
    used_model = None

    for model_name in candidates:
        if response is not None or time.time() - started > GEMINI_TOTAL_SECONDS:
            break

        for attempt in range(2):
            if time.time() - started > GEMINI_TOTAL_SECONDS:
                break

            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
                used_model = model_name
                break

            except errors.ServerError as e:
                last_code = e.code
                logger.warning("assistant: %s answered %s (attempt %s)", model_name, e.code, attempt + 1)

                # "High demand" often clears in a second: retry once on the same model
                if e.code == 503 and attempt == 0:
                    time.sleep(1.5)
                    continue

                break  # otherwise try the next model

            except errors.ClientError as e:
                last_code = e.code
                logger.warning("assistant: %s answered %s: %s", model_name, e.code, e)

                if e.code in (404, 429):
                    break  # this model is not available or its quota is used: try the next

                if e.code in (401, 403):
                    raise HTTPException(
                        status_code=502,
                        detail="The AI service rejected the server's API key.",
                    )

                raise HTTPException(
                    status_code=502,
                    detail="The AI service could not handle that request.",
                )

            except Exception:
                logger.exception("assistant: could not reach Gemini")
                raise HTTPException(
                    status_code=503,
                    detail="Could not reach the AI service. Please try again.",
                )

    if response is None:
        if last_code == 429:
            raise HTTPException(
                status_code=429,
                detail="The free AI quota is used up for now. Please try again in a few minutes.",
            )

        if last_code == 404:
            raise HTTPException(
                status_code=502,
                detail="The AI model name is not available. Check GEMINI_MODEL in backend/.env.",
            )

        raise HTTPException(
            status_code=502,
            detail="The AI service is busy right now. Please try again in a minute.",
        )

    try:
        text = (response.text or "").strip()
    except Exception:
        text = ""

    usage = getattr(response, "usage_metadata", None)

    return (
        text,
        getattr(usage, "prompt_token_count", None),
        getattr(usage, "candidates_token_count", None),
        used_model,
    )


def ask_claude(system_prompt: str, earlier: list, question: str, image):
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="The AI assistant is not set up on the server yet (ANTHROPIC_API_KEY is missing).",
        )

    import anthropic

    client = anthropic.Anthropic(timeout=60.0, max_retries=2)

    content = []

    if image is not None:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image[0],
                    "data": base64.standard_b64encode(image[1]).decode("utf-8"),
                },
            }
        )

    content.append({"type": "text", "text": question})

    messages = earlier + [{"role": "user", "content": content}]

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system_prompt,
            messages=messages,
        )
    except anthropic.RateLimitError:
        raise HTTPException(
            status_code=429,
            detail="The AI service is busy. Please try again in a moment.",
        )
    except anthropic.APIConnectionError:
        raise HTTPException(
            status_code=503,
            detail="Could not reach the AI service. Please try again.",
        )
    except anthropic.APIStatusError as e:
        logger.error("assistant: Claude error %s: %s", e.status_code, e)

        if e.status_code in (401, 403):
            detail = "The AI service rejected the server's API key."
        elif e.status_code == 400:
            detail = "The AI service could not handle that request."
        else:
            detail = "The AI service had a problem. Please try again."

        raise HTTPException(status_code=502, detail=detail)

    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()

    return text, response.usage.input_tokens, response.usage.output_tokens, CLAUDE_MODEL


def ask_ai(system_prompt: str, earlier: list, question: str, image):
    if PROVIDER == "claude":
        return ask_claude(system_prompt, earlier, question, image)

    return ask_gemini(system_prompt, earlier, question, image)


# ============================================================
# THE CHAT ENDPOINT
# ============================================================

@router.post("/sites/{site_id}/shelves/{shelf_id}/assistant/chat")
def chat(
    site_id: int,
    shelf_id: int,
    message: str = Form(...),
    history: str = Form("[]"),
    image: UploadFile | None = File(None),
    user_id: int = Depends(get_current_user_id),
):
    question = message.strip()

    if not question:
        raise HTTPException(status_code=400, detail="Please type a question.")

    if len(question) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=400, detail="That question is too long.")

    check_rate_limit(user_id)

    earlier = parse_history(history)

    # --- optional photo
    prepared_image = None

    if image is not None and image.filename:
        data = image.file.read(MAX_IMAGE_BYTES + 1)

        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="That photo is too large (10 MB maximum).")

        prepared_image = prepare_image(data)

    # --- what FarmTab knows about this shelf.
    # The database connection is closed BEFORE the slow AI call.
    connection = get_connection()

    try:
        check_site_admin_or_staff(connection, site_id, user_id)

        cursor = connection.cursor()
        cursor.execute(
            "SELECT 1 FROM shelves WHERE id = %s AND site_id = %s",
            (shelf_id, site_id),
        )
        found = cursor.fetchone()
        cursor.close()

        if not found:
            raise HTTPException(status_code=404, detail="Shelf not found.")

        farm_context = build_farm_context(connection, site_id, shelf_id)

    finally:
        connection.close()

    started = time.time()

    text, tokens_in, tokens_out, model_name = ask_ai(
        SYSTEM_PROMPT + farm_context,
        earlier,
        question,
        prepared_image,
    )

    logger.info(
        "assistant user=%s provider=%s model=%s image=%s in=%s out=%s seconds=%.1f",
        user_id,
        PROVIDER,
        model_name,
        prepared_image is not None,
        tokens_in,
        tokens_out,
        time.time() - started,
    )

    if not text:
        text = (
            "I could not come up with an answer (the AI may have declined this one). "
            "Please try rephrasing your question."
        )

    return {
        "reply": text,
        "provider": PROVIDER,
        "model": model_name,
        "usage": {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
        },
    }