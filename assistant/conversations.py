"""
Saved AI chats, and the "use a farm photo" option.

This file sits next to assistant.py and re-uses its AI functions, its system
prompt and its farm-data context, so the prompt stays in ONE place (assistant.py).

Endpoints (all under /sites/{site_id}/shelves/{shelf_id}/assistant):
    POST   /send                           ask a question (new or existing chat)
    GET    /conversations                  the user's chats for this shelf
    GET    /conversations/{id}             one chat with all its messages
    DELETE /conversations/{id}             delete a chat
    GET    /messages/{id}/image            the preview of a photo the user attached

A chat is stored only after the AI has answered, so a failed question leaves
nothing half-saved. A chat can only be seen by the user who made it.
"""

import json
import logging
import os
from datetime import timedelta, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from PIL import Image

from assistant.assistant import (
    MAX_HISTORY_MESSAGES,
    MAX_IMAGE_BYTES,
    MAX_MESSAGE_CHARS,
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
# ASK A QUESTION
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
    question = message.strip()

    if not question:
        raise HTTPException(status_code=400, detail="Please type a question.")

    if len(question) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=400, detail="That question is too long.")

    has_upload = image is not None and bool(image.filename)

    if has_upload and layer_image_id is not None:
        raise HTTPException(status_code=400, detail="Please choose only one photo.")

    check_rate_limit(user_id)

    # --- an uploaded photo (read now, before the database work)
    upload_bytes = None

    if has_upload:
        upload_bytes = image.file.read(MAX_IMAGE_BYTES + 1)

        if len(upload_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=400, detail="That photo is too large (10 MB maximum).")

    # --- everything we need from the database. It is closed BEFORE the slow AI call.
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

    # --- ask the AI
    ai_question = f"{farm_note}\n\n{question}" if farm_note else question

    text, tokens_in, tokens_out, model_name = ask_ai(
        SYSTEM_PROMPT + farm_context,
        earlier,
        ai_question,
        prepared_image,
    )

    if not text:
        raise HTTPException(
            status_code=502,
            detail="The AI did not return an answer. Please try rephrasing your question.",
        )

    logger.info(
        "assistant user=%s model=%s photo=%s chat=%s in=%s out=%s",
        user_id,
        model_name,
        image_kind or "none",
        conversation_id or "new",
        tokens_in,
        tokens_out,
    )

    # --- save the question and the answer together
    connection = get_connection()

    try:
        cursor = connection.cursor()

        if conversation_id is None:
            cursor.execute(
                """
                INSERT INTO assistant_conversations (shelf_id, user_id, title)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (shelf_id, user_id, make_title(question)),
            )
            conversation_id = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO assistant_messages
                (conversation_id, role, content, image_kind, layer_image_id, image_thumb)
            VALUES (%s, 'user', %s, %s, %s, %s)
            RETURNING id
            """,
            (conversation_id, question, image_kind, layer_image_id, thumbnail),
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
        "reply": text,
        "model": model_name,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
    }


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
