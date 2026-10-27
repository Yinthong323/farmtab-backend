"""
Saved AI chats, streaming answers, AI-written chat titles, and the
"use a farm photo" option.

This file sits next to assistant.py and re-uses its settings, its system prompt,
its farm-data context and its non-streaming AI function, so the prompt stays in
ONE place (assistant.py).

Endpoints (all under /sites/{site_id}/shelves/{shelf_id}/assistant):
    POST   /send-stream                    ask a question, the answer streams in   (used by the app)
    POST   /send                           ask a question, one JSON answer         (simple / for tests)
    GET    /conversations                  the user's chats for this shelf
    GET    /conversations/{id}             one chat with all its messages
    DELETE /conversations/{id}             delete a chat
    GET    /messages/{id}/image            the preview of a photo the user attached

The streaming answer is a series of lines, each one a small JSON object:
    {"event": "delta", "text": "..."}          a piece of the answer
    {"event": "done", "conversation_id": 7, "title": "...", ...}   the answer is complete and saved
    {"event": "title", "title": "..."}         a better title, written by the AI (new chats only)
    {"event": "error", "detail": "..."}        something failed (nothing is saved)

A chat is stored only after the AI has finished answering, so a failed question
leaves nothing half-saved. A chat can only be seen by the user who made it.
"""

import json
import logging
import os
import time
from datetime import timedelta, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image

from assistant.assistant import (
    GEMINI_CALL_TIMEOUT_MS,
    GEMINI_FALLBACK_MODELS,
    GEMINI_MODEL,
    GEMINI_TOTAL_SECONDS,
    MAX_HISTORY_MESSAGES,
    MAX_IMAGE_BYTES,
    MAX_MESSAGE_CHARS,
    MAX_OUTPUT_TOKENS,
    PROVIDER,
    SYSTEM_PROMPT,
    ask_ai,
    build_farm_context,
    check_rate_limit,
    parse_history,
    prepare_image,
)
from canopy.canopy import DEFAULT_METHOD
from database.connection import get_connection
from shelves.shelves import (
    check_site_admin_or_staff,
    get_current_user_id,
)

logger = logging.getLogger("farmtab.assistant")

router = APIRouter(
    tags=["AI Assistant"],
)

MYT = timezone(timedelta(hours=8))

THUMB_SIDE = 400                # size of the saved preview of an uploaded photo
TITLE_CHARS = 60
PHOTO_ROOT = "uploads/layers"   # farm photos live here (inside the container: /app/uploads/layers)

BASE = "/sites/{site_id}/shelves/{shelf_id}/assistant"


# ============================================================
# SMALL HELPERS
# ============================================================

def check_shelf(cursor, site_id: int, shelf_id: int):
    cursor.execute(
        "SELECT 1 FROM shelves WHERE id = %s AND site_id = %s",
        (shelf_id, site_id),
    )

    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Shelf not found.")


def check_conversation(cursor, conversation_id: int, user_id: int, shelf_id: int):
    """The chat must belong to this user and this shelf (404 otherwise)."""
    cursor.execute(
        """
        SELECT id, title
        FROM assistant_conversations
        WHERE id = %s
          AND user_id = %s
          AND shelf_id = %s
        """,
        (conversation_id, user_id, shelf_id),
    )

    row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Chat not found.")

    return row


def make_title(question: str) -> str:
    """The first title of a chat: the start of the first question."""
    text = " ".join(question.split())

    if len(text) > TITLE_CHARS:
        text = text[: TITLE_CHARS - 3].rstrip() + "..."

    return text or "New chat"


def make_thumbnail(jpeg_bytes: bytes) -> bytes:
    with Image.open(BytesIO(jpeg_bytes)) as image:
        image = image.convert("RGB")
        image.thumbnail((THUMB_SIDE, THUMB_SIDE))

        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=70)

    return buffer.getvalue()


def load_earlier_messages(cursor, conversation_id: int):
    """The last messages of a chat, in the form the AI functions expect."""
    cursor.execute(
        """
        SELECT role, content, image_kind
        FROM (
            SELECT id, role, content, image_kind
            FROM assistant_messages
            WHERE conversation_id = %s
            ORDER BY id DESC
            LIMIT %s
        ) recent
        ORDER BY id
        """,
        (conversation_id, MAX_HISTORY_MESSAGES),
    )

    items = []

    for role, content, image_kind in cursor.fetchall():
        note = "\n[Photo attached]" if image_kind else ""
        items.append({"role": role, "text": content + note})

    return parse_history(json.dumps(items))


def load_farm_photo(cursor, shelf_id: int, layer_image_id: int):
    """
    One of the farm's own camera photos. Returns
    (jpeg bytes, a note that tells the AI what the photo is, a short label).
    """
    cursor.execute(
        """
        SELECT
            i.file_path,
            i.captured_at,
            l.display_name,
            l.code,
            c.transplant_date,
            m.canopy_cm2,
            m.canopy_pixels
        FROM layer_images i
        JOIN layers l
            ON l.id = i.layer_id
        LEFT JOIN layer_cycles c
            ON c.id = i.cycle_id
        LEFT JOIN canopy_measurements m
            ON m.image_id = i.id
           AND m.method = %s
        WHERE i.id = %s
          AND l.shelf_id = %s
        """,
        (DEFAULT_METHOD, layer_image_id, shelf_id),
    )

    row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="That farm photo was not found.")

    file_path, captured_at, name, code, transplant, cm2, pixels = row

    # Only read files inside the farm photo folder
    root = os.path.realpath(PHOTO_ROOT)
    full = os.path.realpath(str(file_path).lstrip("/"))

    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        raise HTTPException(status_code=404, detail="That photo file is no longer available.")

    with open(full, "rb") as f:
        data = f.read()

    local = captured_at.astimezone(MYT) if captured_at else None
    when = local.strftime("%d %b %Y, %H:%M") if local else "an unknown time"

    day = None
    if local and transplant:
        day = (local.date() - transplant).days + 1

    note = (
        f"[Context: the attached photo is a farm camera image of {name} "
        f"(camera {code}), taken {when} Malaysia time"
    )

    if day:
        note += f", on day {day} of its growing cycle"

    note += "."

    if cm2 is not None and pixels is not None:
        note += (
            f" The farm's own measurement of this photo: about {cm2:.0f} cm2 canopy "
            f"({pixels:,} green pixels), a rough estimate from a fixed side-angle camera."
        )

    note += "]"

    label = f"{name}" + (f" \u00B7 Day {day}" if day else "")

    return data, note, label


# ============================================================
# STEP 1 OF A QUESTION: check everything and gather what the AI needs
# ============================================================
# Raises normal HTTP errors (400, 404, 429 ...) when something is wrong.
# The database connection is closed BEFORE the slow AI call.

def prepare_send(
    site_id: int,
    shelf_id: int,
    user_id: int,
    message: str,
    conversation_id,
    layer_image_id,
    image,
):
    question = message.strip()

    if not question:
        raise HTTPException(status_code=400, detail="Please type a question.")

    if len(question) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=400, detail="That question is too long.")

    has_upload = image is not None and bool(image.filename)

    if has_upload and layer_image_id is not None:
        raise HTTPException(status_code=400, detail="Please choose only one photo.")

    check_rate_limit(user_id)

    upload_bytes = None

    if has_upload:
        upload_bytes = image.file.read(MAX_IMAGE_BYTES + 1)

        if len(upload_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="That photo is too large (10 MB maximum).")

    prepared_image = None
    image_kind = None
    thumbnail = None
    farm_note = None
    earlier = []

    connection = get_connection()

    try:
        check_site_admin_or_staff(connection, site_id, user_id)

        cursor = connection.cursor()

        check_shelf(cursor, site_id, shelf_id)

        if conversation_id is not None:
            check_conversation(cursor, conversation_id, user_id, shelf_id)
            earlier = load_earlier_messages(cursor, conversation_id)

        if layer_image_id is not None:
            raw, farm_note, _label = load_farm_photo(cursor, shelf_id, layer_image_id)
            prepared_image = prepare_image(raw)
            image_kind = "farm"

        if upload_bytes is not None:
            prepared_image = prepare_image(upload_bytes)
            thumbnail = make_thumbnail(prepared_image[1])
            image_kind = "upload"

        farm_context = build_farm_context(connection, site_id, shelf_id)

        cursor.close()

    finally:
        connection.close()

    return {
        "question": question,
        "ai_question": f"{farm_note}\n\n{question}" if farm_note else question,
        "system_prompt": SYSTEM_PROMPT + farm_context,
        "earlier": earlier,
        "image": prepared_image,
        "image_kind": image_kind,
        "thumbnail": thumbnail,
        "layer_image_id": layer_image_id,
        "conversation_id": conversation_id,
        "shelf_id": shelf_id,
        "user_id": user_id,
    }


# ============================================================
# STEP 3 OF A QUESTION: save the question and the answer together
# ============================================================

def save_exchange(prepared: dict, text: str, model_name, tokens_in, tokens_out) -> dict:
    connection = get_connection()

    try:
        cursor = connection.cursor()

        conversation_id = prepared["conversation_id"]
        is_new = conversation_id is None

        if is_new:
            cursor.execute(
                """
                INSERT INTO assistant_conversations (shelf_id, user_id, title)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (prepared["shelf_id"], prepared["user_id"], make_title(prepared["question"])),
            )
            conversation_id = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO assistant_messages
                (conversation_id, role, content, image_kind, layer_image_id, image_thumb)
            VALUES (%s, 'user', %s, %s, %s, %s)
            RETURNING id
            """,
            (
                conversation_id,
                prepared["question"],
                prepared["image_kind"],
                prepared["layer_image_id"],
                prepared["thumbnail"],
            ),
        )
        user_message_id = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO assistant_messages
                (conversation_id, role, content, model, tokens_in, tokens_out)
            VALUES (%s, 'assistant', %s, %s, %s, %s)
            RETURNING id
            """,
            (conversation_id, text, model_name, tokens_in, tokens_out),
        )
        assistant_message_id = cursor.fetchone()[0]

        cursor.execute(
            """
            UPDATE assistant_conversations
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING title
            """,
            (conversation_id,),
        )
        title = cursor.fetchone()[0]

        connection.commit()
        cursor.close()

    except Exception:
        connection.rollback()
        logger.exception("assistant: could not save the chat")
        raise HTTPException(status_code=500, detail="The answer could not be saved.")

    finally:
        connection.close()

    return {
        "conversation_id": conversation_id,
        "title": title,
        "is_new": is_new,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
    }


# ============================================================
# THE QUESTION WITH ONE JSON ANSWER (no streaming)
# ============================================================

@router.post(BASE + "/send")
def send_message(
    site_id: int,
    shelf_id: int,
    message: str = Form(...),
    conversation_id: int | None = Form(None),
    layer_image_id: int | None = Form(None),
    image: UploadFile | None = File(None),
    user_id: int = Depends(get_current_user_id),
):
    prepared = prepare_send(
        site_id, shelf_id, user_id, message, conversation_id, layer_image_id, image
    )

    text, tokens_in, tokens_out, model_name = ask_ai(
        prepared["system_prompt"],
        prepared["earlier"],
        prepared["ai_question"],
        prepared["image"],
    )

    if not text:
        raise HTTPException(
            status_code=502,
            detail="The AI did not return an answer. Please try rephrasing your question.",
        )

    logger.info(
        "assistant user=%s model=%s photo=%s chat=%s in=%s out=%s",
        user_id, model_name, prepared["image_kind"] or "none",
        conversation_id or "new", tokens_in, tokens_out,
    )

    saved = save_exchange(prepared, text, model_name, tokens_in, tokens_out)

    return {
        "conversation_id": saved["conversation_id"],
        "title": saved["title"],
        "reply": text,
        "model": model_name,
        "user_message_id": saved["user_message_id"],
        "assistant_message_id": saved["assistant_message_id"],
    }


# ============================================================
# STREAMING
# ============================================================

class StreamError(Exception):
    """Something went wrong while the answer was being produced."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _chunk_text(chunk) -> str:
    try:
        return chunk.text or ""
    except Exception:
        return ""


def stream_answer(system_prompt: str, earlier: list, question: str, image):
    """
    Produces the answer piece by piece. Yields:
        ("delta", "some text")   as the answer arrives
        ("final", {...})         once, at the end (tokens and the model that answered)
    Raises StreamError when it cannot answer.
    """
    # Only Gemini streams. Other providers answer in one piece.
    if PROVIDER != "gemini":
        try:
            text, tokens_in, tokens_out, model_name = ask_ai(system_prompt, earlier, question, image)
        except HTTPException as e:
            raise StreamError(str(e.detail))

        yield ("delta", text)
        yield ("final", {"tokens_in": tokens_in, "tokens_out": tokens_out, "model": model_name})
        return

    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise StreamError("The AI assistant is not set up on the server yet (GEMINI_API_KEY is missing).")

    from google import genai
    from google.genai import errors, types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=GEMINI_CALL_TIMEOUT_MS),
    )

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

    candidates = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]

    started = time.time()
    last_code = None

    for model_name in candidates:
        if time.time() - started > GEMINI_TOTAL_SECONDS:
            break

        for attempt in range(2):
            if time.time() - started > GEMINI_TOTAL_SECONDS:
                break

            sent_text = False
            usage = None

            try:
                stream = client.models.generate_content_stream(
                    model=model_name,
                    contents=contents,
                    config=config,
                )

                for chunk in stream:
                    metadata = getattr(chunk, "usage_metadata", None)

                    if metadata is not None:
                        usage = metadata

                    piece = _chunk_text(chunk)

                    if piece:
                        sent_text = True
                        yield ("delta", piece)

                if not sent_text:
                    raise StreamError(
                        "The AI did not return an answer. Please try rephrasing your question."
                    )

                yield (
                    "final",
                    {
                        "tokens_in": getattr(usage, "prompt_token_count", None),
                        "tokens_out": getattr(usage, "candidates_token_count", None),
                        "model": model_name,
                    },
                )
                return

            except StreamError:
                raise

            except errors.ServerError as e:
                last_code = e.code
                logger.warning("assistant: %s answered %s (attempt %s)", model_name, e.code, attempt + 1)

                # Once text has reached the app we cannot switch models any more
                if sent_text:
                    raise StreamError("The connection to the AI was interrupted. Please retry.")

                if e.code == 503 and attempt == 0:
                    time.sleep(1.5)
                    continue

                break

            except errors.ClientError as e:
                last_code = e.code
                logger.warning("assistant: %s answered %s: %s", model_name, e.code, e)

                if sent_text:
                    raise StreamError("The connection to the AI was interrupted. Please retry.")

                if e.code in (404, 429):
                    break

                if e.code in (401, 403):
                    raise StreamError("The AI service rejected the server's API key.")

                raise StreamError("The AI service could not handle that request.")

            except Exception:
                logger.exception("assistant: could not reach Gemini")

                if sent_text:
                    raise StreamError("The connection to the AI was interrupted. Please retry.")

                raise StreamError("Could not reach the AI service. Please try again.")

    if last_code == 429:
        raise StreamError("The free AI quota is used up for now. Please try again in a few minutes.")

    if last_code == 404:
        raise StreamError("The AI model name is not available. Check GEMINI_MODEL in backend/.env.")

    raise StreamError("The AI service is busy right now. Please try again in a minute.")


# ------------------------------------------------------------
# Chat titles written by the AI
# ------------------------------------------------------------

TITLE_PROMPT = """Write a short title for a chat that starts with the question and answer below.
Rules: at most 5 words; use the same language as the question; no quotes, no markdown, no full stop at the end.
Reply with the title only.

Question: {question}

Answer: {answer}"""


def clean_title(text) -> str | None:
    if not text:
        return None

    for line in str(text).splitlines():
        title = line.strip().strip("\"'`*#").strip()

        if title.lower().startswith("title:"):
            title = title[6:].strip()

        title = title.rstrip(".").strip()

        if title:
            return title[:TITLE_CHARS]

    return None


def generate_title(question: str, answer: str) -> str | None:
    """A short title for a new chat. Returns None when it cannot (the first title stays)."""
    api_key = os.getenv("GEMINI_API_KEY")

    if PROVIDER != "gemini" or not api_key:
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=20_000),
        )

        prompt = TITLE_PROMPT.format(question=question[:400], answer=answer[:600])

        # At most two models, so a busy service never delays things for long
        for model_name in ([GEMINI_MODEL] + GEMINI_FALLBACK_MODELS)[:2]:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(max_output_tokens=256),
                )

                title = clean_title(response.text)

                if title:
                    return title
            except Exception:
                logger.warning("assistant: title from %s failed", model_name)

    except Exception:
        logger.exception("assistant: could not write a chat title")

    return None


def save_title(conversation_id: int, user_id: int, title: str):
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE assistant_conversations
            SET title = %s
            WHERE id = %s
              AND user_id = %s
            """,
            (title, conversation_id, user_id),
        )
        connection.commit()
        cursor.close()
    except Exception:
        connection.rollback()
        logger.exception("assistant: could not save the chat title")
    finally:
        connection.close()


def _line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


def event_stream(prepared: dict):
    """The lines sent to the app while a question is being answered."""
    pieces = []
    final = {}
    started = time.time()

    try:
        for kind, payload in stream_answer(
            prepared["system_prompt"],
            prepared["earlier"],
            prepared["ai_question"],
            prepared["image"],
        ):
            if kind == "delta":
                if payload:
                    pieces.append(payload)
                    yield _line({"event": "delta", "text": payload})
            else:
                final = payload

    except StreamError as e:
        yield _line({"event": "error", "detail": e.detail})
        return

    except Exception:
        logger.exception("assistant: streaming failed")
        yield _line({"event": "error", "detail": "The AI service had a problem. Please try again."})
        return

    text = "".join(pieces).strip()

    if not text:
        yield _line({"event": "error", "detail": "The AI did not return an answer. Please try rephrasing your question."})
        return

    try:
        saved = save_exchange(
            prepared,
            text,
            final.get("model"),
            final.get("tokens_in"),
            final.get("tokens_out"),
        )
    except HTTPException as e:
        yield _line({"event": "error", "detail": str(e.detail)})
        return

    logger.info(
        "assistant user=%s model=%s photo=%s chat=%s in=%s out=%s seconds=%.1f (streamed)",
        prepared["user_id"],
        final.get("model"),
        prepared["image_kind"] or "none",
        saved["conversation_id"],
        final.get("tokens_in"),
        final.get("tokens_out"),
        time.time() - started,
    )

    yield _line(
        {
            "event": "done",
            "conversation_id": saved["conversation_id"],
            "title": saved["title"],
            "model": final.get("model"),
            "user_message_id": saved["user_message_id"],
            "assistant_message_id": saved["assistant_message_id"],
        }
    )

    # The answer is already on the screen. A better title follows a moment later.
    if saved["is_new"]:
        title = generate_title(prepared["question"], text)

        if title:
            save_title(saved["conversation_id"], prepared["user_id"], title)
            yield _line({"event": "title", "title": title})


@router.post(BASE + "/send-stream")
def send_message_stream(
    site_id: int,
    shelf_id: int,
    message: str = Form(...),
    conversation_id: int | None = Form(None),
    layer_image_id: int | None = Form(None),
    image: UploadFile | None = File(None),
    user_id: int = Depends(get_current_user_id),
):
    # Problems with the request itself (empty question, unknown chat, wrong
    # shelf ...) are normal HTTP errors, raised BEFORE the stream starts.
    prepared = prepare_send(
        site_id, shelf_id, user_id, message, conversation_id, layer_image_id, image
    )

    return StreamingResponse(
        event_stream(prepared),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # tells a proxy such as nginx not to hold the text back
        },
    )


# ============================================================
# CHAT HISTORY
# ============================================================

@router.get(BASE + "/conversations")
def list_conversations(
    site_id: int,
    shelf_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        check_site_admin_or_staff(connection, site_id, user_id)

        cursor = connection.cursor()

        check_shelf(cursor, site_id, shelf_id)

        cursor.execute(
            """
            SELECT
                c.id,
                c.title,
                c.updated_at,
                (
                    SELECT COUNT(*)
                    FROM assistant_messages m
                    WHERE m.conversation_id = c.id
                ) AS message_count
            FROM assistant_conversations c
            WHERE c.user_id = %s
              AND c.shelf_id = %s
            ORDER BY c.updated_at DESC
            LIMIT 50
            """,
            (user_id, shelf_id),
        )

        rows = cursor.fetchall()
        cursor.close()

        return [
            {
                "id": row[0],
                "title": row[1],
                "updated_at": row[2].astimezone(MYT).isoformat(),
                "message_count": row[3],
            }
            for row in rows
        ]

    finally:
        connection.close()


@router.get(BASE + "/conversations/{conversation_id}")
def get_conversation(
    site_id: int,
    shelf_id: int,
    conversation_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        check_site_admin_or_staff(connection, site_id, user_id)

        cursor = connection.cursor()

        check_shelf(cursor, site_id, shelf_id)

        _id, title = check_conversation(cursor, conversation_id, user_id, shelf_id)

        cursor.execute(
            """
            SELECT
                m.id,
                m.role,
                m.content,
                m.image_kind,
                (m.image_thumb IS NOT NULL) AS has_thumb,
                m.created_at,
                i.file_path,
                l.display_name
            FROM assistant_messages m
            LEFT JOIN layer_images i
                ON i.id = m.layer_image_id
            LEFT JOIN layers l
                ON l.id = i.layer_id
            WHERE m.conversation_id = %s
            ORDER BY m.id
            """,
            (conversation_id,),
        )

        rows = cursor.fetchall()
        cursor.close()

        messages = []

        for mid, role, content, kind, has_thumb, created_at, file_path, layer_name in rows:
            photo = None

            if kind == "upload":
                photo = {"kind": "upload", "available": bool(has_thumb)}
            elif kind == "farm":
                photo = {
                    "kind": "farm",
                    "available": file_path is not None,
                    "url": file_path,
                    "label": layer_name,
                }

            messages.append(
                {
                    "id": mid,
                    "role": role,
                    "text": content,
                    "created_at": created_at.astimezone(MYT).isoformat(),
                    "photo": photo,
                }
            )

        return {"id": conversation_id, "title": title, "messages": messages}

    finally:
        connection.close()


@router.delete(BASE + "/conversations/{conversation_id}")
def delete_conversation(
    site_id: int,
    shelf_id: int,
    conversation_id: int,
    user_id: int = Depends(get_current_user_id),
):
    connection = get_connection()

    try:
        check_site_admin_or_staff(connection, site_id, user_id)

        cursor = connection.cursor()

        check_shelf(cursor, site_id, shelf_id)

        cursor.execute(
            """
            DELETE FROM assistant_conversations
            WHERE id = %s
              AND user_id = %s
              AND shelf_id = %s
            RETURNING id
            """,
            (conversation_id, user_id, shelf_id),
        )

        deleted = cursor.fetchone()

        if not deleted:
            connection.rollback()
            raise HTTPException(status_code=404, detail="Chat not found.")

        connection.commit()
        cursor.close()

        return {"deleted": True}

    finally:
        connection.close()


@router.get(BASE + "/messages/{message_id}/image")
def get_message_image(
    site_id: int,
    shelf_id: int,
    message_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """The preview of a photo the user attached. Only its owner can load it."""
    connection = get_connection()

    try:
        check_site_admin_or_staff(connection, site_id, user_id)

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT m.image_thumb
            FROM assistant_messages m
            JOIN assistant_conversations c
                ON c.id = m.conversation_id
            WHERE m.id = %s
              AND c.user_id = %s
              AND c.shelf_id = %s
              AND m.image_thumb IS NOT NULL
            """,
            (message_id, user_id, shelf_id),
        )

        row = cursor.fetchone()
        cursor.close()

        if not row:
            raise HTTPException(status_code=404, detail="Photo not found.")

        return Response(
            content=bytes(row[0]),
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400"},
        )

    finally:
        connection.close()
