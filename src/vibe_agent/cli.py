import argparse
import asyncio
import json

from vibe_agent.config import get_settings
from vibe_agent.service import ContentAgent


async def run_collect() -> None:
    result = await ContentAgent(get_settings()).collect_and_rank()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect")
    args = parser.parse_args()
    if args.command == "collect":
        asyncio.run(run_collect())
