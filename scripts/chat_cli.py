"""Local terminal chat loop against the compiled graph - no Telegram, no
network dependency beyond the LLM proxy + Chroma. This is the primary tool
for developing/debugging graph logic quickly.

Usage:
    python scripts/chat_cli.py [--thread-id X] [--persist]

--persist uses AsyncSqliteSaver (checkpoints.sqlite) so state survives
restarts; without it, uses in-memory MemorySaver (fresh state every run).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


async def run(thread_id: str, persist: bool) -> None:
    from langchain_core.messages import HumanMessage

    from app.graph.builder import build_graph

    config = {"configurable": {"thread_id": thread_id}}

    if persist:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        from app.config import get_settings

        conn = await aiosqlite.connect(str(get_settings().sqlite_path))
        checkpointer = AsyncSqliteSaver(conn)
        graph = build_graph(checkpointer)
    else:
        from langgraph.checkpoint.memory import MemorySaver

        graph = build_graph(MemorySaver())
        conn = None

    print(f"[chat_cli] thread_id={thread_id} persist={persist}. Ctrl+C to quit.\n")

    try:
        while True:
            try:
                user_text = input("شما: ").strip()
            except EOFError:
                break
            if not user_text:
                continue
            if user_text in {"exit", "quit", ":q"}:
                break

            result = await graph.ainvoke({"messages": [HumanMessage(content=user_text)]}, config=config)
            print(f"سارا: {result.get('final_response', '(بدون پاسخ)')}\n")
    finally:
        if conn is not None:
            await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread-id", default="cli-dev")
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()

    asyncio.run(run(args.thread_id, args.persist))


if __name__ == "__main__":
    main()
