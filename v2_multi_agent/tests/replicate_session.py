"""Replicate user session b90272d2 — 4 turns in order."""
import asyncio, aiohttp, json, time

BASE = "http://localhost:5055/pyapi"

QUERIES = [
    "Section 35 of BNS",
    "find relevent supreme court cases",
    "can you elaborate the salil cases in detail",
    "Salil Mahajan vs Avinash Kumar",
]


async def main():
    thread_id = None
    async with aiohttp.ClientSession() as session:
        for i, q in enumerate(QUERIES, 1):
            payload = {"Promptquery": q}
            if thread_id:
                payload["globalThreadId"] = thread_id

            print(f"\n{'='*60}")
            print(f"TURN {i}: \"{q}\"")
            print(f"{'='*60}")
            start = time.time()

            async with session.post(f"{BASE}/search", json=payload) as resp:
                data = await resp.json()
                elapsed = time.time() - start

                if resp.status != 200:
                    print(f"ERROR {resp.status}: {data}")
                    continue

                thread_id = data.get("globalThreadId", thread_id)
                agents = data.get("agents_used", [])
                tokens = data.get("total_tokens_consumed", 0)
                result = data.get("result", "")
                sources = data.get("source", [])
                rewritten = data.get("query_rewritten", False)
                eff_query = data.get("effective_query", "")

                print(f"Thread: {thread_id[:12]}...")
                print(f"Agents: {agents} | Tokens: {tokens} | Time: {elapsed:.1f}s")
                if rewritten:
                    print(f"Query rewritten to: {eff_query[:150]}")
                print(f"Sources: {len(sources)}")
                print(f"\nResponse ({len(result)} chars):")
                print("-" * 40)
                print(result[:3000])
                if len(result) > 3000:
                    print(f"\n... [{len(result) - 3000} more chars]")
                print("-" * 40)


if __name__ == "__main__":
    asyncio.run(main())
