"""Feature 2 — Advisor: a streaming Gemini chat agent grounded in the people database.

The agent sees an index of everyone, the full profiles of the people the user is
focused on, and can call tools to read any other profile, search all notes, or
open the detailed analysis behind a citation like [s3]."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, AsyncIterator

from google.genai import types

from .config import Settings
from .gemini import GeminiService
from .prompts import ADVISOR_SYSTEM
from .store import Store, StoreError

MAX_TOOL_ROUNDS = 6
MAX_HISTORY = 40
MAX_FOCUS = 5

TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="list_people",
        description="List everyone in the knowledge base with id, relationship, tags and one-line summary.",
    ),
    types.FunctionDeclaration(
        name="get_person_profile",
        description="Read the full Markdown profile of one person (includes their Source Log).",
        parameters_json_schema={
            "type": "object",
            "properties": {"person": {"type": "string", "description": "Person id (e.g. 'jane-doe') or name."}},
            "required": ["person"],
        },
    ),
    types.FunctionDeclaration(
        name="search_notes",
        description=(
            "Keyword search across every profile and source analysis. Use it for questions spanning several "
            "people or for specific details (e.g. 'birthday', 'allergic', 'hiking')."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A few keywords."}},
            "required": ["query"],
        },
    ),
    types.FunctionDeclaration(
        name="get_source",
        description=(
            "Open the detailed analysis behind a citation such as [s3]: observations with evidence, quotes, "
            "visible text, fact-check verdicts and web sources."
        ),
        parameters_json_schema={
            "type": "object",
            "properties": {
                "person": {"type": "string", "description": "Person id or name."},
                "source_id": {"type": "string", "description": "Source id such as 's3'."},
            },
            "required": ["person", "source_id"],
        },
    ),
]


class Advisor:
    def __init__(self, store: Store, gem: GeminiService, settings: Settings):
        self.store = store
        self.gem = gem
        self.settings = settings

    # ------------------------------------------------------------------ prompt
    def people_index(self) -> tuple[int, str]:
        people = self.store.list_people()
        lines = []
        for p in people:
            line = f"- {p.name} (id: {p.slug})"
            if p.relationship:
                line += f" — {p.relationship}"
            if p.summary:
                line += f" — {p.summary}"
            if p.aliases:
                line += f" (aka {', '.join(p.aliases)})"
            if p.properties.get("priority") is not None:
                line += f" [priority {p.properties['priority']}]"
            if p.properties.get("follow_up"):
                line += " [follow up]"
            lines.append(line)
        return len(people), "\n".join(lines) or "(empty — suggest adding someone on the Consume page)"

    def system_prompt(self, focus: list[str]) -> str:
        count, index = self.people_index()
        blocks = []
        seen: set[str] = set()
        for ref in focus[:MAX_FOCUS]:
            slug = self.store.resolve(ref)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            person = self.store.get(slug)
            blocks.append(
                f"\n=== PROFILE: {person.name} (id: {slug}) ===\n{person.raw.strip()}\n=== END PROFILE: {person.name} ==="
            )
        focus_text = ""
        if blocks:
            focus_text = "\nThe user is currently focused on the following people; their full profiles are included:\n" + "\n".join(blocks)
        return ADVISOR_SYSTEM.format(today=date.today().isoformat(), count=count, index=index, focus=focus_text)

    # ------------------------------------------------------------------- tools
    def execute_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "list_people":
                return {"people": [p.to_summary() for p in self.store.list_people()]}
            if name == "get_person_profile":
                ref = str(args.get("person", ""))
                slug = self.store.resolve(ref)
                if not slug:
                    names = ", ".join(p.name for p in self.store.list_people()) or "nobody yet"
                    return {"error": f"No person matching {ref!r}. People in the knowledge base: {names}."}
                return {"id": slug, "profile": self.store.get(slug).raw}
            if name == "search_notes":
                results = self.store.search(str(args.get("query", "")), limit=12)
                return {"results": results} if results else {"results": [], "note": "No matches."}
            if name == "get_source":
                ref = str(args.get("person", ""))
                slug = self.store.resolve(ref)
                if not slug:
                    return {"error": f"No person matching {ref!r}."}
                record, text = self.store.get_source(slug, str(args.get("source_id", "")))
                return {"person": slug, "source_id": record.id, "markdown": text}
            return {"error": f"Unknown tool {name!r}."}
        except StoreError as exc:
            return {"error": str(exc)}

    def tool_label(self, name: str, args: dict[str, Any]) -> str:
        if name == "list_people":
            return "Looking through everyone"
        if name == "get_person_profile":
            slug = self.store.resolve(str(args.get("person", "")))
            who = self.store.get(slug).name if slug else args.get("person", "?")
            return f"Reading {who}'s profile"
        if name == "search_notes":
            return f"Searching notes for “{args.get('query', '')}”"
        if name == "get_source":
            slug = self.store.resolve(str(args.get("person", "")))
            who = self.store.get(slug).name if slug else args.get("person", "?")
            return f"Opening source {args.get('source_id', '?')} of {who}"
        return f"Using {name}"

    # -------------------------------------------------------------------- chat
    async def chat(self, messages: list[dict[str, str]], focus: list[str]) -> AsyncIterator[dict[str, Any]]:
        contents: list[types.Content] = []
        for msg in messages[-MAX_HISTORY:]:
            text = (msg.get("content") or "").strip()
            if not text:
                continue
            role = "model" if msg.get("role") == "assistant" else "user"
            contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
        if not contents or contents[-1].role != "user":
            yield {"type": "error", "message": "The last message must come from the user."}
            return

        config = types.GenerateContentConfig(
            system_instruction=self.system_prompt(focus),
            tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        for _round in range(MAX_TOOL_ROUNDS + 1):
            stream = await self.gem.stream(
                model=self.settings.model_advisor,
                contents=contents,
                config=config,
                thinking=self.settings.advisor_thinking,
            )
            parts: list[types.Part] = []
            calls: list[types.FunctionCall] = []
            async for chunk in stream:
                candidate = (chunk.candidates or [None])[0]
                if candidate is None or candidate.content is None or not candidate.content.parts:
                    continue
                for part in candidate.content.parts:
                    parts.append(part)  # keep as-is: thought signatures must round-trip
                    if part.function_call:
                        calls.append(part.function_call)
                    elif part.text and not part.thought:
                        yield {"type": "text", "delta": part.text}
            if not calls:
                break
            if _round == MAX_TOOL_ROUNDS:
                yield {"type": "text", "delta": "\n\n_(Stopped: too many lookups in one answer.)_"}
                break
            contents.append(types.Content(role="model", parts=parts))
            responses = []
            for call in calls:
                args = dict(call.args or {})
                yield {"type": "tool", "name": call.name, "label": self.tool_label(call.name or "", args)}
                result = await asyncio.to_thread(self.execute_tool, call.name or "", args)
                responses.append(
                    types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response=result))
                )
            contents.append(types.Content(role="user", parts=responses))
        yield {"type": "done"}
