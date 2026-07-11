"""Query rewrite, parallel recall, rerank, and empty-safe fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from packages.knowledge_service.src.retrieve import retrieve_documents
from packages.observability.src.tracer import trace_event

from apps.agent_service.src.agent.llm_client import LLMClient, LLMMessage
from apps.agent_service.src.agent.runtime.mcp.tool_layer import ToolCallResult, get_mcp_tool_layer
from packages.agent.src.types import SessionContext


async def rewrite_query(
    query: str,
    *,
    llm_client: LLMClient | None = None,
    model: str = "deepseek/deepseek-v4-flash",
    n: int = 3,
) -> list[str]:
    """Expand a query into multiple search angles."""
    client = llm_client or LLMClient(default_model=model)
    prompt = (
        f"You are a search query optimizer for a retrieval system.\n"
        f"Rewrite the original user query into {n} distinct search sub-queries that "
        f"maximize recall by covering different angles: paraphrases, synonyms, "
        f"and broader or narrower phrasings that surface the user's implicit intent.\n"
        f"Guidelines:\n"
        f"- Each sub-query must be self-contained and keyword-rich.\n"
        f"- Do not repeat the original query verbatim.\n"
        f"- Preserve the original language of the query.\n"
        f"Return only a JSON array of {n} strings, with no extra text or explanation.\n"
        f'Original query: "{query}"'
    )
    try:
        response = await client.complete(
            [LLMMessage(role="user", content=prompt)],
            model=model,
            temperature=0.3,
            max_tokens=256,
            name="mcp.retrieval.rewrite",
        )
        raw = response.text
        start, end = raw.find("["), raw.rfind("]") + 1
        queries = json.loads(raw[start:end])
        if not isinstance(queries, list):
            return [query]
        cleaned = [str(item) for item in queries if str(item).strip()]
        # always keep the original query; dict.fromkeys preserves insertion order
        return list(dict.fromkeys([query, *cleaned]))
    except Exception:
        return [query]


async def rerank_candidates(
    query: str,
    items: list[dict[str, Any]],
    *,
    top_k: int,
    llm_client: LLMClient | None = None,
    model: str = "deepseek/deepseek-v4-flash",
) -> list[dict[str, Any]]:
    """Score merged candidates and return the top-K."""
    if len(items) <= top_k:
        return items

    client = llm_client or LLMClient(default_model=model)
    items_text = "\n".join(
        f"{index}. {json.dumps(item, default=str)[:200]}"
        for index, item in enumerate(items)
    )
    prompt = (
        f'Rank the following retrieval results for query "{query}". '
        f"Return only a JSON array of indices from most to least relevant.\n{items_text}"
    )
    try:
        response = await client.complete(
            [LLMMessage(role="user", content=prompt)],
            model=model,
            temperature=0.0,
            max_tokens=256,
            name="mcp.retrieval.rerank",
        )
        raw = response.text
        start, end = raw.find("["), raw.rfind("]") + 1
        order = json.loads(raw[start:end])
        reranked = [items[index] for index in order if isinstance(index, int) and 0 <= index < len(items)]
        return reranked[:top_k] if reranked else items[:top_k]
    except Exception:
        return items[:top_k]


async def retrieve_with_optimization(
    *,
    tool_name: str,
    query: str,
    ctx: SessionContext,
    params: dict[str, Any] | None = None,
    top_k: int = 5,
    llm_client: LLMClient | None = None,
    model: str = "deepseek/deepseek-v4-flash",
) -> ToolCallResult:
    """Run rewrite -> parallel recall -> merge/dedupe -> rerank -> fallback."""
    layer = get_mcp_tool_layer()
    # step 1: expand one query into multiple retrieval angles
    sub_queries = await rewrite_query(query, llm_client=llm_client, model=model, n=3)
    trace_event("mcp.retrieval.rewrite", {"query": query, "sub_queries": sub_queries})

    # step 2: recall each sub-query in parallel through the MCP layer
    recall_tasks = []
    for sub_query in sub_queries:
        call_params = dict(params or {})
        call_params["query"] = sub_query
        call_params.setdefault("tenant_id", ctx.tenant_id)
        recall_tasks.append(layer.call(tool_name, call_params, ctx, use_cache=True))

    recall_results = await asyncio.gather(*recall_tasks, return_exceptions=True)

    # step 3: merge and dedupe by content hash across all sub-query recalls
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for result in recall_results:
        if not isinstance(result, ToolCallResult) or not result.success:
            continue
        matches = result.data.get("matches", [])
        if not isinstance(matches, list):
            continue
        for item in matches:
            if not isinstance(item, dict):
                continue
            key = hashlib.md5(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

    if not merged:
        kb_matches = await retrieve_documents(
            tenant_id=ctx.tenant_id,
            query=query,
            collection="playbooks",
            limit=top_k,
        )
        merged.extend(item for item in kb_matches if isinstance(item, dict))

    if not merged:
        return ToolCallResult(
            success=True,
            data={
                "matches": [],
                "reason": "empty_knowledge_base",
                "query": query,
                "sub_queries": sub_queries,
            },
            tool_name=tool_name,
        )

    reranked = await rerank_candidates(
        query,
        merged,
        top_k=top_k,
        llm_client=llm_client,
        model=model,
    )
    return ToolCallResult(
        success=True,
        data={"matches": reranked, "query": query, "sub_queries": sub_queries, "reranked": True},
        tool_name=tool_name,
    )


__all__ = ["rewrite_query", "rerank_candidates", "retrieve_with_optimization"]
