"""Structural guard: no blocking I/O directly inside an async function.

WHY THIS EXISTS
===============
This codebase runs on a single asyncio event loop. The Elasticsearch client
(`opensearch-py`) and the ChromaDB reader are both **synchronous**. Calling
either directly from an `async def` freezes the entire loop for the duration
of the round trip — every other in-flight request stalls, not just the one
that made the call.

The established pattern here is `await asyncio.to_thread(...)`. Two call
sites had been missed:

  * `agents/newacts.py` — the retry-path `es.search()` on a retrieval miss.
    Bit only under load AND on a miss, which is why it survived.
  * `agents/drafting.py` — `get_full_attachment.invoke()`, a ChromaDB read
    returning the ENTIRE uploaded document (up to ~3.5M chars for a 500-page
    PDF). `agents/document.py` made the same call correctly; drafting did not.

Pinning those two line numbers would be brittle and would not stop the third
instance. This test instead walks the AST of every agent module and fails on
any *new* one.

HEURISTIC (and its limits)
==========================
A call is flagged when it matches a known-blocking pattern AND appears
lexically inside an `async def`, but NOT inside:
  - a nested plain `def` (those are the `_build_and_search`-style helpers that
    exist precisely to be handed to `to_thread`), or
  - a `lambda` (same reason — `to_thread(lambda: ...)` is the idiomatic form
    when arguments need binding).

That means a nested sync helper which is *called directly* rather than passed
to `to_thread` would slip through. Accepted: the false-negative is rarer than
the false-positive would be annoying, and the helpers in this repo are all
to_thread-dispatched.

Run:
    pytest tests/test_no_blocking_calls.py -v
    python tests/test_no_blocking_calls.py     # no pytest required
"""

from __future__ import annotations

import ast
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

AGENTS_DIR = os.path.join(_REPO_ROOT, "agents")

# Synchronous objects whose `.search(...)` blocks. Matched on the receiver name.
_BLOCKING_SEARCH_RECEIVERS = {"es", "_client", "client"}

# Synchronous LangChain tools whose `.invoke(...)` blocks on I/O.
# Extend this when a new sync tool is introduced.
_BLOCKING_TOOL_INVOKES = {
    "get_full_attachment",
    "retrieve_attachment_context",
    "search_pdf_collection",
    "store_pdf_chunks",
}


def _describe(node: ast.Call) -> str | None:
    """Return a label if this call is known-blocking, else None."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    receiver = func.value
    if not isinstance(receiver, ast.Name):
        return None

    if func.attr == "search" and receiver.id in _BLOCKING_SEARCH_RECEIVERS:
        return f"{receiver.id}.search(...)"
    if func.attr == "invoke" and receiver.id in _BLOCKING_TOOL_INVOKES:
        return f"{receiver.id}.invoke(...)"
    return None


def _offending_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """All blocking calls sitting directly in an async body."""
    found: list[tuple[int, str]] = []

    def walk(node, *, in_async: bool, shielded: bool):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef):
                walk(child, in_async=True, shielded=False)
                continue
            # A nested plain def or a lambda is dispatched elsewhere —
            # conventionally handed to asyncio.to_thread.
            if isinstance(child, (ast.FunctionDef, ast.Lambda)):
                walk(child, in_async=in_async, shielded=True)
                continue
            if isinstance(child, ast.Call) and in_async and not shielded:
                label = _describe(child)
                if label:
                    found.append((child.lineno, label))
            walk(child, in_async=in_async, shielded=shielded)

    walk(tree, in_async=False, shielded=False)
    return found


def _agent_modules() -> list[str]:
    return sorted(
        os.path.join(AGENTS_DIR, f)
        for f in os.listdir(AGENTS_DIR)
        if f.endswith(".py") and not f.startswith("__")
    )


class TestNoBlockingCallsInAsync:

    def test_no_blocking_io_in_async_functions(self):
        violations: list[str] = []
        for path in _agent_modules():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            rel = os.path.relpath(path, _REPO_ROOT).replace("\\", "/")
            for lineno, label in _offending_calls(tree):
                violations.append(f"{rel}:{lineno}  {label}")

        assert not violations, (
            "Blocking I/O called directly from an async function — this freezes "
            "the event loop for every concurrent request.\n\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n\nWrap it: `await asyncio.to_thread(fn, *args)`, or "
              "`await asyncio.to_thread(lambda: obj.invoke({...}))` when you need "
              "to bind arguments. See agents/document.py:238-243 for the pattern."
        )

    def test_the_guard_actually_detects_violations(self):
        """A guard that cannot fail is worthless — prove it catches the shape."""
        bad = ast.parse(
            "import asyncio\n"
            "async def handler():\n"
            "    return es.search(index='i', body={})\n"
        )
        assert _offending_calls(bad) == [(3, "es.search(...)")]

        bad_invoke = ast.parse(
            "async def handler():\n"
            "    return get_full_attachment.invoke({'collection_id': 'c'})\n"
        )
        assert _offending_calls(bad_invoke) == [
            (2, "get_full_attachment.invoke(...)")]

    def test_the_guard_allows_the_correct_patterns(self):
        """to_thread with a nested helper, and to_thread with a lambda."""
        via_helper = ast.parse(
            "import asyncio\n"
            "async def handler():\n"
            "    def _run():\n"
            "        return es.search(index='i', body={})\n"
            "    return await asyncio.to_thread(_run)\n"
        )
        assert _offending_calls(via_helper) == []

        via_lambda = ast.parse(
            "import asyncio\n"
            "async def handler():\n"
            "    return await asyncio.to_thread(\n"
            "        lambda: get_full_attachment.invoke({'collection_id': 'c'}))\n"
        )
        assert _offending_calls(via_lambda) == []

    def test_sync_functions_are_not_flagged(self):
        """Blocking calls in sync code are fine — that is what threads are for."""
        sync_only = ast.parse(
            "def _run():\n"
            "    return es.search(index='i', body={})\n"
        )
        assert _offending_calls(sync_only) == []


# --- Standalone runner (venv has no pytest) ---------------------------------

if __name__ == "__main__":
    failures = 0
    inst = TestNoBlockingCallsInAsync()
    for name in sorted(n for n in dir(inst) if n.startswith("test_")):
        try:
            getattr(inst, name)()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {name}:\n{e}")
        except Exception as e:
            failures += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print("\nALL PASSED" if not failures else f"\n{failures} FAILURE(S)")
    sys.exit(1 if failures else 0)
