import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatSession, ChatMessage
from app.services.advisor.llm_provider import LLMProvider, openai_tool_schema
from app.services.advisor.prompt import get_system_prompt
from app.services.advisor.tool_registry import execute_tool

MAX_CONTEXT_MESSAGES = 10
MAX_TOOL_ROUNDS = 3
REFERENTIAL_TERMS = (
    "that", "this month", "last month", "compare", "same", "only",
    "again", "those", "it", "instead",
)


async def create_session(
    db: AsyncSession, user_id: uuid.UUID, title: Optional[str] = None,
) -> ChatSession:
    session = ChatSession(user_id=user_id, title=title or "New conversation")
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_sessions(db: AsyncSession, user_id: uuid.UUID) -> List[ChatSession]:
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc())
    )
    return list(result.scalars().unique().all())


async def get_session(
    db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID,
) -> ChatSession:
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id,
        )
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


def _is_referential_followup(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in REFERENTIAL_TERMS)


def _extract_recent_session_context(messages: List[ChatMessage]) -> dict[str, Any]:
    """
    Reconstruct lightweight session memory from recent tool call args:
    last date range and optional account/category focus.
    """
    for m in reversed(messages):
        if m.role != "assistant" or not m.tool_name or not m.tool_payload:
            continue

        args = m.tool_payload or {}
        period_a = args.get("period_a") or {}
        period_b = args.get("period_b") or {}

        date_from = args.get("date_from") or period_b.get("date_from") or period_a.get("date_from")
        date_to = args.get("date_to") or period_b.get("date_to") or period_a.get("date_to")
        category_ids = args.get("category_ids")
        account_ids = args.get("account_ids")

        if date_from or date_to or category_ids or account_ids:
            return {
                "date_from": date_from,
                "date_to": date_to,
                "category_ids": category_ids,
                "account_ids": account_ids,
                "last_tool": m.tool_name,
            }

    return {}


def _format_assistant_response(text: str) -> str:
    trimmed = (text or "").strip()
    if not trimmed:
        return "Direct answer:\nI could not produce an answer from available data."

    lowered = trimmed.lower()
    if "insights:" in lowered and "next actions:" in lowered:
        return trimmed

    return (
        "Direct answer:\n"
        f"{trimmed}\n\n"
        "Insights:\n"
        f"- {trimmed}\n\n"
        "Next actions:\n"
        "- Ask a follow-up with a specific category/account if you want tighter guidance."
    )


def _build_context(messages: List[ChatMessage], pending_user_text: Optional[str] = None) -> list[dict]:
    """Build the LLM messages array from recent chat history.

    The system prompt is generated at call time so the embedded date resolution
    table always reflects the real server date, not an import-time snapshot.
    """
    context: list[dict] = [{"role": "system", "content": get_system_prompt(date.today())}]
    if pending_user_text and _is_referential_followup(pending_user_text):
        memory = _extract_recent_session_context(messages[-MAX_CONTEXT_MESSAGES:])
        if memory:
            context.append({
                "role": "system",
                "content": (
                    "SESSION MEMORY (use only if the current user message is referential): "
                    f"last_date_from={memory.get('date_from')}, "
                    f"last_date_to={memory.get('date_to')}, "
                    f"category_ids={memory.get('category_ids')}, "
                    f"account_ids={memory.get('account_ids')}, "
                    f"last_tool={memory.get('last_tool')}."
                ),
            })
    recent = messages[-MAX_CONTEXT_MESSAGES:]
    for m in recent:
        if m.role == "tool":
            payload = m.tool_payload or {}
            content = json.dumps(payload)[:2000]
            context.append({
                "role": "tool",
                "tool_call_id": f"call_{m.tool_name}",
                "content": content,
            })
        elif m.role == "assistant" and m.tool_name:
            context.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": f"call_{m.tool_name}",
                    "type": "function",
                    "function": {
                        "name": m.tool_name,
                        "arguments": json.dumps(m.tool_payload or {}),
                    },
                }],
            })
        else:
            context.append({"role": m.role, "content": m.content or ""})
    return context


async def send_message(
    db: AsyncSession,
    user_id: uuid.UUID,
    llm: LLMProvider,
    content: str,
    session_id: Optional[uuid.UUID] = None,
) -> tuple[ChatSession, ChatMessage]:
    if session_id:
        session = await get_session(db, user_id, session_id)
    else:
        session = await create_session(db, user_id, title=content[:80])

    user_msg = ChatMessage(session_id=session.id, role="user", content=content)
    db.add(user_msg)
    await db.flush()

    all_messages = list(session.messages) + [user_msg]
    tools_schema = openai_tool_schema()

    for _ in range(MAX_TOOL_ROUNDS):
        context = _build_context(all_messages, pending_user_text=content)

        try:
            resp = await llm.chat_completion(context, tools=tools_schema)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LLM service is unavailable. Please try again later.",
            )

        choice = resp["choices"][0]
        msg = choice["message"]

        if msg.get("tool_calls"):
            tc = msg["tool_calls"][0]
            fn_name = tc["function"]["name"]
            try:
                fn_args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                fn_args = {}

            tool_call_msg = ChatMessage(
                session_id=session.id, role="assistant", content="",
                tool_name=fn_name, tool_payload=fn_args,
            )
            db.add(tool_call_msg)
            await db.flush()

            tool_result = await execute_tool(fn_name, db, user_id, fn_args)

            tool_result_msg = ChatMessage(
                session_id=session.id, role="tool", content="",
                tool_name=fn_name, tool_payload=tool_result,
            )
            db.add(tool_result_msg)
            await db.flush()

            all_messages.extend([tool_call_msg, tool_result_msg])
            continue

        assistant_text = _format_assistant_response(msg.get("content") or "I could not generate a response.")
        assistant_msg = ChatMessage(
            session_id=session.id, role="assistant", content=assistant_text,
        )
        db.add(assistant_msg)
        session.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(assistant_msg)
        return session, assistant_msg

    fallback = ChatMessage(
        session_id=session.id, role="assistant",
        content="I gathered the data but could not formulate a response. Please try rephrasing your question.",
    )
    db.add(fallback)
    session.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(fallback)
    return session, fallback
