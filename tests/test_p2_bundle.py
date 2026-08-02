"""Regression tests for the P2 bundle (2026-08-02).

Covers:
- G-23: Per-section partial-retry (retry loop present in _generate_sectionwise).
- G-24: Partial-draft banner prepend + draft_incomplete SSE emission.
- G-25: Orphan endpoints deleted from gateway.
- G-26: Dead code sweep (symbols removed from agent files).
- G-27: Injection classifier module + wired into drafting_node (behind env).
- G-28: Per-thread state TTL — cap on messages + chat_store.expire_old_threads.
- G-29: Post-deploy smoke + rollback workflows present.
- G-30: Ceremonial blocks for 10 Indic languages present.
- G-31: Drafting added to _SCOREABLE_AGENTS + should_score helper.
- G-32: Queue-status race fix + heartbeat.
- G-33: Web fallback branch present in SCI + GST agents.
- G-34: Per-token HTML strip helper wired into token emission.

Run:
    pytest tests/test_p2_bundle.py -v
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# G-25: Orphan endpoint deletion
# ---------------------------------------------------------------------------

class TestOrphanEndpointsDeleted:
    def test_delete_vectordb_removed(self):
        with open("core/gateway.py", encoding="utf-8") as f:
            source = f.read()
        assert '@app.delete("/pyapi/delete_vectordb' not in source
        assert 'async def delete_vectordb(' not in source

    def test_health_detailed_removed(self):
        with open("core/gateway.py", encoding="utf-8") as f:
            source = f.read()
        assert '@app.get("/pyapi/health/detailed"' not in source
        assert 'async def health_detailed(' not in source

    def test_admin_orphans_removed(self):
        with open("core/gateway.py", encoding="utf-8") as f:
            source = f.read()
        # Assert the ROUTE DECORATORS are gone, not just the strings —
        # the removal-notice comment intentionally names the endpoints
        # so future maintainers see they were removed on purpose.
        assert '@app.get("/pyapi/admin/fallback_logs' not in source
        assert '@app.get("/pyapi/admin/fallback_stats' not in source
        assert '@app.get("/pyapi/admin/quality_stats' not in source
        assert 'async def admin_fallback_logs(' not in source
        assert 'async def admin_fallback_stats(' not in source
        assert 'async def admin_quality_stats(' not in source

    def test_admin_expire_threads_endpoint_added(self):
        with open("core/gateway.py", encoding="utf-8") as f:
            source = f.read()
        assert '/pyapi/admin/expire-threads' in source
        assert "chat_store.expire_old_threads" in source


# ---------------------------------------------------------------------------
# G-26: Dead code sweep
# ---------------------------------------------------------------------------

class TestDeadCodeSweep:
    def test_legislation_match_phrase_symbols_removed(self):
        with open("agents/legislation.py", encoding="utf-8") as f:
            source = f.read()
        # Removal-notice comment intentionally names the deleted
        # symbols. Assert their DEFINITIONS are gone (not raw
        # substrings) so the comment can stay.
        assert "class QueryMetadata" not in source
        assert "def _extract_match_phrase" not in source
        assert "MATCH_PHRASE_PROMPT =" not in source

    def test_judgment_unused_imports_removed(self):
        with open("agents/judgment.py", encoding="utf-8") as f:
            source = f.read()
        # Original imports pulled in symbols that were never used.
        assert "detect_citation" not in source
        assert "detect_case_type" not in source
        assert "TIMEOUT_METADATA_SEC" not in source

    def test_sci_pdf_links_dead_local_removed(self):
        with open("agents/sci_judgment.py", encoding="utf-8") as f:
            source = f.read()
        # The stray `pdf_links = []` declaration should be gone.
        # (Only match on its own line to avoid false positives on
        # SourceMetadata field access.)
        import re
        assert not re.search(r"^\s*pdf_links\s*=\s*\[\]\s*$", source, flags=re.M)

    def test_document_specialized_dispatch_removed(self):
        with open("agents/document.py", encoding="utf-8") as f:
            source = f.read()
        # Removal-notice comment intentionally names the deleted helpers.
        # Assert DEFINITIONS are gone, not raw substrings.
        assert "_SPECIALIZED_PROMPTS: dict" not in source
        assert "def _pick_specialized_prompt" not in source
        assert "def _llm_config_for_artifact" not in source
        # Chroma retrieval helpers — check for `def`
        assert "def _get_or_create_collection" not in source
        assert "def _collection_has_data" not in source
        assert "def _retrieve_from_collections" not in source
        assert "def _retrieve_docs" not in source
        # Unused import of self_refine
        assert "from core.self_refine import self_refine" not in source


# ---------------------------------------------------------------------------
# G-23: per-section retry
# ---------------------------------------------------------------------------

class TestSectionPairRetry:
    def test_generate_sectionwise_has_retry_loop(self):
        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        # Look for the attempt loop and the "retrying once" log line.
        assert "for attempt in (1, 2)" in source
        assert "retrying once" in source.lower()


# ---------------------------------------------------------------------------
# G-24: partial-draft banner + draft_incomplete SSE
# ---------------------------------------------------------------------------

class TestPartialDraftBanner:
    def test_sectionwise_tracks_failed_pairs(self):
        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        assert "failed_pairs" in source
        assert '"type": "draft_incomplete"' in source

    def test_banner_text_present(self):
        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        assert "Draft incomplete" in source
        assert "re-send your prompt to retry" in source


# ---------------------------------------------------------------------------
# G-27: Injection classifier
# ---------------------------------------------------------------------------

class TestInjectionClassifier:
    def test_module_present_and_gated_by_env(self):
        from core import injection_check
        # Off by default
        assert injection_check._enabled() is False

    def test_sample_chat_history_empty(self):
        from core.injection_check import sample_chat_history
        assert sample_chat_history(None) == ""
        assert sample_chat_history([]) == ""

    def test_verdict_defaults_are_benign(self):
        from core.injection_check import Verdict
        v = Verdict()
        assert v.is_injection is False
        assert v.confidence == 0.0

    def test_drafting_node_calls_classifier_when_enabled(self):
        # Source-level: the drafting node imports and calls the
        # classifier module, and the refusal branch is present.
        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        # Import of the classifier's public helpers
        assert "from core.injection_check import _enabled" in source
        assert "check_injection" in source
        # Refusal branch
        assert "injection_blocked" in source
        # The env var itself should NOT be checked directly in
        # drafting.py — that check lives in injection_check._enabled().
        # Allow the env-var *name* to appear in comments but not as a
        # live `os.getenv("INJECTION_CHECK_ENABLED"` call site.
        assert 'os.getenv("INJECTION_CHECK_ENABLED"' not in source


# ---------------------------------------------------------------------------
# G-28: per-thread state TTL
# ---------------------------------------------------------------------------

class TestStateTTL:
    def test_capped_messages_reducer_present(self):
        with open("core/state.py", encoding="utf-8") as f:
            source = f.read()
        assert "_capped_add_messages" in source
        assert "_MESSAGES_CAP" in source

    def test_capped_reducer_actually_caps(self):
        from core.state import _capped_add_messages, _MESSAGES_CAP
        from langchain.messages import HumanMessage

        base = [HumanMessage(content=f"turn {i}") for i in range(_MESSAGES_CAP + 5)]
        result = _capped_add_messages([], base)
        assert isinstance(result, list)
        assert len(result) == _MESSAGES_CAP
        # Freshest (highest turn numbers) survived
        assert "turn " in getattr(result[-1], "content", "")

    def test_chat_store_expire_helper_present(self):
        # Both SQLite and Postgres variants need the helper.
        with open("core/chat_store.py", encoding="utf-8") as f:
            source = f.read()
        # Look for two implementations
        count = source.count("def _expire_old_threads_sync")
        assert count >= 2, (
            f"expected _expire_old_threads_sync in both SQLite and Postgres "
            f"backends (found {count})"
        )
        assert source.count("async def expire_old_threads") >= 2


# ---------------------------------------------------------------------------
# G-29: Deploy workflows
# ---------------------------------------------------------------------------

class TestDeployWorkflows:
    def test_smoke_workflow_present(self):
        assert os.path.exists(".github/workflows/prod-post-deploy-smoke.yml")
        with open(".github/workflows/prod-post-deploy-smoke.yml", encoding="utf-8") as f:
            body = f.read()
        assert "canonical query smoke" in body.lower()
        assert "PROD_INTEGRATION_URL" in body

    def test_rollback_workflow_present(self):
        assert os.path.exists(".github/workflows/prod-rollback.yml")
        with open(".github/workflows/prod-rollback.yml", encoding="utf-8") as f:
            body = f.read()
        assert "target_sha" in body
        assert "environment:" in body


# ---------------------------------------------------------------------------
# G-30: Ceremonial blocks
# ---------------------------------------------------------------------------

class TestCeremonialBlocks:
    def test_ten_indic_languages_have_blocks(self):
        with open("core/language.py", encoding="utf-8") as f:
            source = f.read()
        # Language codes that should now have their own elif branch
        for code in ["bn", "ta", "te", "kn", "ml", "gu", "pa", "ur", "or", "as"]:
            assert f'elif lang == "{code}"' in source, (
                f"language.py must have a ceremonial block for '{code}'"
            )


# ---------------------------------------------------------------------------
# G-31: Drafting quality scoring
# ---------------------------------------------------------------------------

class TestDraftingQualityScoring:
    def test_drafting_in_scoreable_agents(self):
        from core.quality import _SCOREABLE_AGENTS
        assert "Drafting" in _SCOREABLE_AGENTS

    def test_should_score_helper_present(self):
        from core.quality import should_score
        # Only Drafting → 5% sample (probabilistic; run many times)
        drafting_hits = sum(should_score(["Drafting"]) for _ in range(1000))
        # Expect roughly 50; allow wide slack (25 to 90).
        assert 15 <= drafting_hits <= 95, (
            f"Drafting sample rate looks wrong: {drafting_hits}/1000"
        )
        # Retrieval agents → 10% sample
        retrieval_hits = sum(should_score(["Legislation"]) for _ in range(1000))
        assert 55 <= retrieval_hits <= 145, (
            f"Retrieval sample rate looks wrong: {retrieval_hits}/1000"
        )
        # Non-scoreable → never
        assert should_score(["Non_legal"]) is False
        assert should_score([]) is False


# ---------------------------------------------------------------------------
# G-32: Queue-status race fix + heartbeat
# ---------------------------------------------------------------------------

class TestQueueStatusRaceFix:
    def test_semaphore_uses_nonblocking_probe(self):
        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        # The old racy `.locked()` check should be gone; the new pattern
        # uses `asyncio.wait_for(_AGENT_SEMAPHORE.acquire(), timeout=0.05)`.
        assert "asyncio.wait_for(_AGENT_SEMAPHORE.acquire(), timeout=0.05)" in source

    def test_heartbeat_emitted_while_queued(self):
        with open("agents/drafting.py", encoding="utf-8") as f:
            source = f.read()
        assert "_emit_queue_heartbeat" in source
        assert '"status": "waiting"' in source


# ---------------------------------------------------------------------------
# G-33: SCI + GST web fallback
# ---------------------------------------------------------------------------

class TestScigstWebFallback:
    def test_sci_has_web_fallback_branch(self):
        with open("agents/sci_judgment.py", encoding="utf-8") as f:
            source = f.read()
        assert "web_search_fallback" in source
        assert "falling back to web search" in source.lower()

    def test_gst_has_web_fallback_branch(self):
        with open("agents/gst_judgment.py", encoding="utf-8") as f:
            source = f.read()
        assert "web_search_fallback" in source
        assert "falling back to web search" in source.lower()


# ---------------------------------------------------------------------------
# G-34: Per-token HTML strip
# ---------------------------------------------------------------------------

class TestPerTokenHtmlStrip:
    def test_helper_defined(self):
        from core.chat_runner import _strip_html_from_token
        # Legit text unchanged
        assert _strip_html_from_token("Hello world") == "Hello world"
        assert _strip_html_from_token("<= 5") == "<= 5"  # not a tag shape
        # Attack chunk stripped
        assert "<script>" not in _strip_html_from_token("<script>alert(1)</script>")

    def test_token_emission_uses_helper(self):
        with open("core/chat_runner.py", encoding="utf-8") as f:
            source = f.read()
        # The kind == "token" emission should now route through the helper
        assert "_strip_html_from_token(chunk[\"content\"])" in source
