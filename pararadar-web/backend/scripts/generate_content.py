"""One bounded request; credentials are read only from NVIDIA_API_KEY."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import HTTPException

from nvidia import ContentRequest, Nvidia, NvidiaSettings


async def main():
    parser = argparse.ArgumentParser(description="Türkçe video senaryosu üret")
    parser.add_argument("topic")
    parser.add_argument("--category", choices=["finance", "crypto"], default="finance")
    parser.add_argument("--duration", type=int, choices=[30, 60, 90], default=60)
    args = parser.parse_args()
    request = ContentRequest(
        topic=args.topic, category=args.category, duration_seconds=args.duration
    )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        try:
            result = await Nvidia(NvidiaSettings.from_env(), client).generate(request)
        except HTTPException as exc:
            print(
                f"Generation failed ({exc.status_code}): {exc.detail}", file=sys.stderr
            )
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
