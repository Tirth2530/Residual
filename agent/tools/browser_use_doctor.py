"""Check Browser Use before the demo. Never prints your key.

    python tools/browser_use_doctor.py            # READ-ONLY: is the key accepted? (lists your recent tasks)
    python tools/browser_use_doctor.py --run      # one small real task on Wikipedia through the same client the Hands use (costs a little)
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.browser_use import BASE, BrowserUseCloud, BrowserUseError  # noqa: E402


def load_key() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(os.environ.get("RESIDUAL_ENV_FILE") or Path(__file__).resolve().parent.parent / ".env"))
    except ImportError:
        pass
    return (os.environ.get("BROWSER_USE_API_KEY") or "").strip()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()
    key = load_key()
    if not key:
        print("BROWSER_USE_API_KEY isn't set in agent/.env")
        return 1
    print(f"key found ({len(key)} characters). Talking to {BASE}")
    bu = BrowserUseCloud(key)
    try:
        r = await bu._request("GET", "/tasks?pageSize=1")
        print("key accepted: yes. recent tasks on this account:", r.get("totalItems", "?") if isinstance(r, dict) else "?")
    except BrowserUseError as e:
        print("key accepted: NO ->", e)
        return 2
    except Exception as e:
        print("couldn't reach Browser Use:", repr(e)[:200])
        return 2
    if not args.run:
        print("read-only check done. Add --run to try one small real task.")
        return 0
    t0 = time.time()
    try:
        s = await bu.open("https://en.wikipedia.org/wiki/Amyotrophic_lateral_sclerosis")
        print(f"[{time.time() - t0:4.1f}s] session opened. live view: {s.get('live_url')}")
        out = await bu.run(s["session_id"], "Read the first sentence of this article and return it as the summary. Do not click anything.",
                           schema={"type": "object", "properties": {"status": {"type": "string"}, "summary": {"type": "string"}}, "required": ["status", "summary"]},
                           max_steps=6, timeout=120)
        print(f"[{time.time() - t0:4.1f}s] answer: {str(out)[:300]}")
        await bu.close(s["session_id"])
    except Exception as e:
        print(f"[{time.time() - t0:4.1f}s] FAILED: {e}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
