from __future__ import annotations

import difflib
import importlib.util
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Protocol


class SemanticMemorySource(Protocol):
    def retrieve_semantic(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        ...


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return int(default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalize_text(value: str) -> str:
    return " ".join(
        "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in str(value or "").lower()).split()
    )


def _token_set(value: str) -> set[str]:
    return {token for token in _normalize_text(value).split() if token}


def _parse_timestamp(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class DialogueRetriever:
    """Retrieves and ranks compact memory snippets for dialogue context."""

    def __init__(self, source: SemanticMemorySource) -> None:
        self.source = source
        self._embedding_cache: dict[str, list[float]] = {}
        self._flag_reranker: Any = None
        self._flag_reranker_error = False
        self._embed_rerank_unavailable = False

        self.base_overlap_weight = _env_float("JARVIS_DIALOGUE_RANK_WEIGHT_OVERLAP", 0.36)
        self.thread_overlap_weight = _env_float("JARVIS_DIALOGUE_RANK_WEIGHT_THREAD", 0.18)
        self.sequence_weight = _env_float("JARVIS_DIALOGUE_RANK_WEIGHT_SEQUENCE", 0.16)
        self.confidence_weight = _env_float("JARVIS_DIALOGUE_RANK_WEIGHT_CONFIDENCE", 0.2)
        self.freshness_weight = _env_float("JARVIS_DIALOGUE_RANK_WEIGHT_FRESHNESS", 0.1)
        self.min_score = _env_float("JARVIS_DIALOGUE_MIN_SCORE", 0.0)
        self.default_limit = max(1, _env_int("JARVIS_DIALOGUE_RETRIEVE_LIMIT", 8))
        self.default_candidate_limit = max(4, _env_int("JARVIS_DIALOGUE_RETRIEVE_CANDIDATE_LIMIT", 32))
        self.embed_blend_weight = max(0.0, min(1.0, _env_float("JARVIS_DIALOGUE_EMBED_BLEND_WEIGHT", 0.3)))
        self.flag_blend_weight = max(0.0, min(1.0, _env_float("JARVIS_DIALOGUE_FLAG_BLEND_WEIGHT", 0.4)))

    def get_config(self) -> dict[str, Any]:
        flag_installed = importlib.util.find_spec("FlagEmbedding") is not None
        return {
            "enabled": True,
            "limit": self.default_limit,
            "candidate_limit": self.default_candidate_limit,
            "weights": {
                "overlap": self.base_overlap_weight,
                "thread": self.thread_overlap_weight,
                "sequence": self.sequence_weight,
                "confidence": self.confidence_weight,
                "freshness": self.freshness_weight,
            },
            "min_score": self.min_score,
            "embed_rerank": {
                "enabled": _env_bool("JARVIS_DIALOGUE_EMBED_RERANK_ENABLED", True),
                "model": str(os.getenv("JARVIS_DIALOGUE_EMBED_MODEL") or "mxbai-embed-large").strip(),
                "blend_weight": self.embed_blend_weight,
                "available": not self._embed_rerank_unavailable,
            },
            "flag_rerank": {
                "enabled": _env_bool("JARVIS_DIALOGUE_FLAG_RERANK_ENABLED", False),
                "model": str(os.getenv("JARVIS_DIALOGUE_FLAG_RERANK_MODEL") or "BAAI/bge-reranker-v2-m3").strip(),
                "blend_weight": self.flag_blend_weight,
                "installed": bool(flag_installed),
                "available": bool(flag_installed and not self._flag_reranker_error),
            },
        }

    def retrieve(
        self,
        *,
        query: str,
        extra_queries: list[str] | None = None,
        thread_terms: list[str] | None = None,
        limit: int = 8,
        candidate_limit: int = 32,
    ) -> dict[str, Any]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return {"snippets": [], "candidate_count": 0, "strategy": {"base": "semantic_like"}}

        all_queries = [normalized_query]
        for item in list(extra_queries or []):
            text = str(item or "").strip()
            if text and text not in all_queries:
                all_queries.append(text)
        resolved_candidate_limit = max(4, int(candidate_limit or self.default_candidate_limit))
        resolved_limit = max(1, int(limit or self.default_limit))
        per_query_limit = max(4, int(resolved_candidate_limit // max(1, len(all_queries))))

        dedupe: dict[str, dict[str, Any]] = {}
        for item_query in all_queries:
            rows = self.source.retrieve_semantic(item_query, limit=per_query_limit)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                payload = row.get("answer_payload") if isinstance(row.get("answer_payload"), dict) else {}
                memory_key = str(payload.get("memory_key") or "").strip()
                text_value = str(payload.get("text") or "").strip()
                if not text_value:
                    continue
                dedupe_key = f"{memory_key}|{text_value}"
                existing = dedupe.get(dedupe_key)
                confidence = float(row.get("confidence") or 0.0)
                freshness = str(row.get("freshness") or "").strip() or None
                if existing and float(existing.get("confidence") or 0.0) >= confidence:
                    continue
                dedupe[dedupe_key] = {
                    "memory_key": memory_key or None,
                    "text": text_value,
                    "confidence": confidence,
                    "freshness": freshness,
                    "source_query": item_query,
                    "provenance": row.get("provenance") if isinstance(row.get("provenance"), dict) else {},
                }

        candidates = list(dedupe.values())
        ranked = self._rank_lexical(
            query=normalized_query,
            candidates=candidates,
            thread_terms=list(thread_terms or []),
        )

        strategy = {
            "base": "semantic_like",
            "queries": all_queries,
            "candidate_limit": int(resolved_candidate_limit),
            "embedding_rerank": False,
            "flag_rerank": False,
        }
        if _env_bool("JARVIS_DIALOGUE_EMBED_RERANK_ENABLED", True):
            reranked = self._rerank_with_embeddings(query=normalized_query, candidates=ranked)
            if reranked:
                ranked = reranked
                strategy["embedding_rerank"] = True
        if _env_bool("JARVIS_DIALOGUE_FLAG_RERANK_ENABLED", False):
            reranked = self._rerank_with_flag_reranker(query=normalized_query, candidates=ranked)
            if reranked:
                ranked = reranked
                strategy["flag_rerank"] = True
        if self.min_score > 0.0:
            ranked = [item for item in ranked if float(item.get("score") or 0.0) >= self.min_score]

        compact: list[dict[str, Any]] = []
        for idx, item in enumerate(ranked[:resolved_limit], start=1):
            compact.append(
                {
                    "rank": idx,
                    "memory_key": item.get("memory_key"),
                    "text": item.get("text"),
                    "confidence": round(float(item.get("confidence") or 0.0), 4),
                    "freshness": item.get("freshness"),
                    "score": round(float(item.get("score") or 0.0), 6),
                    "source_query": item.get("source_query"),
                }
            )
        return {
            "snippets": compact,
            "candidate_count": len(candidates),
            "strategy": strategy,
            "tuning": self.get_config(),
        }

    def _rank_lexical(
        self,
        *,
        query: str,
        candidates: list[dict[str, Any]],
        thread_terms: list[str],
    ) -> list[dict[str, Any]]:
        query_norm = _normalize_text(query)
        query_tokens = _token_set(query_norm)
        thread_text = " ".join(str(item or "") for item in thread_terms)
        thread_tokens = _token_set(thread_text)
        now = datetime.now(timezone.utc)
        ranked: list[dict[str, Any]] = []
        for item in candidates:
            text = str(item.get("text") or "")
            text_norm = _normalize_text(text)
            text_tokens = _token_set(text_norm)
            overlap = 0.0
            if query_tokens:
                overlap = len(query_tokens.intersection(text_tokens)) / max(1, len(query_tokens))
            thread_overlap = 0.0
            if thread_tokens:
                thread_overlap = len(thread_tokens.intersection(text_tokens)) / max(1, len(thread_tokens))
            seq_ratio = difflib.SequenceMatcher(a=query_norm, b=text_norm).ratio()
            confidence = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
            freshness_score = 0.0
            parsed_ts = _parse_timestamp(str(item.get("freshness") or ""))
            if parsed_ts is not None:
                age_days = max(0.0, (now - parsed_ts).total_seconds() / 86400.0)
                freshness_score = math.exp(-age_days / 45.0)
            score = (
                (self.base_overlap_weight * overlap)
                + (self.thread_overlap_weight * thread_overlap)
                + (self.sequence_weight * seq_ratio)
                + (self.confidence_weight * confidence)
                + (self.freshness_weight * freshness_score)
            )
            ranked_item = dict(item)
            ranked_item["score"] = float(score)
            ranked.append(ranked_item)
        ranked.sort(
            key=lambda item: (
                float(item.get("score") or 0.0),
                float(item.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        return ranked

    def _ollama_embed_endpoint(self) -> str:
        explicit = str(os.getenv("JARVIS_OLLAMA_EMBED_ENDPOINT") or "").strip()
        if explicit:
            return explicit
        generate_endpoint = str(os.getenv("JARVIS_OLLAMA_ENDPOINT") or "http://127.0.0.1:11434/api/generate").strip()
        parsed = urllib.parse.urlparse(generate_endpoint)
        if not parsed.scheme or not parsed.netloc:
            return "http://127.0.0.1:11434/api/embed"
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/api/embed", "", "", ""))

    def _embed_text(self, text: str) -> list[float] | None:
        if self._embed_rerank_unavailable:
            return None
        normalized = str(text or "").strip()
        if not normalized:
            return None
        if normalized in self._embedding_cache:
            return list(self._embedding_cache[normalized])
        endpoint = self._ollama_embed_endpoint()
        model = str(os.getenv("JARVIS_DIALOGUE_EMBED_MODEL") or "mxbai-embed-large").strip() or "mxbai-embed-large"
        payload = {"model": model, "input": normalized}
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=1.4) as resp:  # noqa: S310 - local endpoint by policy
                raw = resp.read().decode("utf-8")
            parsed = json.loads(raw)
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError, urllib.error.URLError):
            self._embed_rerank_unavailable = True
            return None
        vector: list[float] | None = None
        if isinstance(parsed.get("embedding"), list):
            vector = [float(x) for x in parsed.get("embedding") if isinstance(x, (int, float))]
        elif isinstance(parsed.get("embeddings"), list) and parsed.get("embeddings"):
            first = parsed.get("embeddings")[0]
            if isinstance(first, list):
                vector = [float(x) for x in first if isinstance(x, (int, float))]
        if not vector:
            self._embed_rerank_unavailable = True
            return None
        self._embedding_cache[normalized] = list(vector)
        return vector

    def _rerank_with_embeddings(
        self,
        *,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        if not candidates:
            return None
        query_vec = self._embed_text(query)
        if not query_vec:
            return None
        reranked: list[dict[str, Any]] = []
        for item in candidates:
            text = str(item.get("text") or "")
            if not text:
                continue
            item_vec = self._embed_text(text)
            if not item_vec:
                continue
            sim = _cosine_similarity(query_vec, item_vec)
            merged = dict(item)
            merged["score"] = ((1.0 - self.embed_blend_weight) * float(item.get("score") or 0.0)) + (
                self.embed_blend_weight * max(0.0, sim)
            )
            reranked.append(merged)
        if not reranked:
            return None
        reranked.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return reranked

    def _get_flag_reranker(self) -> Any | None:
        if self._flag_reranker_error:
            return None
        if self._flag_reranker is not None:
            return self._flag_reranker
        try:
            from FlagEmbedding import FlagReranker  # type: ignore
        except Exception:
            self._flag_reranker_error = True
            return None
        model_name = str(os.getenv("JARVIS_DIALOGUE_FLAG_RERANK_MODEL") or "BAAI/bge-reranker-v2-m3").strip()
        try:
            self._flag_reranker = FlagReranker(model_name, use_fp16=False)
        except Exception:
            self._flag_reranker_error = True
            return None
        return self._flag_reranker

    def _rerank_with_flag_reranker(
        self,
        *,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        reranker = self._get_flag_reranker()
        if reranker is None or not candidates:
            return None
        pairs = [[str(query), str(item.get("text") or "")] for item in candidates if str(item.get("text") or "").strip()]
        if not pairs:
            return None
        try:
            scores = reranker.compute_score(pairs, normalize=True)
        except Exception:
            return None
        if isinstance(scores, (int, float)):
            score_values = [float(scores)]
        elif isinstance(scores, list):
            score_values = [float(item) for item in scores]
        else:
            return None
        reranked: list[dict[str, Any]] = []
        for idx, item in enumerate(candidates[: len(score_values)]):
            merged = dict(item)
            normalized = max(0.0, min(1.0, float(score_values[idx])))
            merged["score"] = ((1.0 - self.flag_blend_weight) * float(item.get("score") or 0.0)) + (
                self.flag_blend_weight * normalized
            )
            reranked.append(merged)
        if not reranked:
            return None
        reranked.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        return reranked
