#!/usr/bin/env python3
"""删除知识空间里的首页或指定标题节点；默认只预演，加 --yes 才删除。"""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

from deal_pilot.feishu.api import PublishAPI, WikiAPI  # noqa: E402

INDEX_TITLE = "首页"


async def run(
    *,
    index: bool,
    title: str | None,
    node_token: str | None,
    space_name: str | None,
    yes: bool,
) -> int:
    if not index and not title and not node_token:
        print("请指定 --index、--title 或 --node-token")
        return 1
    wiki, publish = WikiAPI(), PublishAPI()
    spaces = await wiki.list_spaces()
    if space_name:
        spaces = [space for space in spaces if space.name == space_name]
        if not spaces:
            print(f"找不到知识空间：{space_name}")
            return 1
    failures = 0
    if node_token:
        if not space_name:
            print("删除单个节点时需要用 --space 指定知识空间")
            return 1
        space = spaces[0]
        if not yes:
            print(f"[预演] 将删除 {space.name} 的节点 {node_token}")
            return 0
        try:
            await publish.delete_wiki_node(space_id=space.space_id, node_token=node_token)
            print(f"[删除] {space.name} / {node_token}")
        except Exception as error:  # noqa: BLE001 逐条报告，最后统一失败退出
            failures += 1
            print(f"[失败] {space.name} / {node_token}：{type(error).__name__}: {error}")
        return failures
    for space in spaces:
        for node in await wiki.list_nodes(space.space_id):
            target = INDEX_TITLE if index else title
            if node.title != target:
                continue
            if not yes:
                print(f"[预演] 将删除 {space.name} / {node.title}（{node.node_token}）")
                continue
            try:
                await publish.delete_wiki_node(
                    space_id=space.space_id, node_token=node.node_token
                )
                print(f"[删除] {space.name} / {node.title}（{node.node_token}）")
            except Exception as error:  # noqa: BLE001 逐条报告，最后统一失败退出
                failures += 1
                print(f"[失败] {space.name} / {node.title}：{type(error).__name__}: {error}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", action="store_true", help="删除各知识空间的首页")
    parser.add_argument("--title", default=None, help="删除指定标题的节点")
    parser.add_argument("--node-token", default=None, help="删除指定 node_token，需配合 --space")
    parser.add_argument("--space", default=None, help="只处理指定名称的知识空间")
    parser.add_argument("--yes", action="store_true", help="确认删除，否则只预演")
    args = parser.parse_args()
    failures = asyncio.run(
        run(index=args.index, title=args.title, node_token=args.node_token,
            space_name=args.space, yes=args.yes)
    )
    if failures:
        print(f"\n有 {failures} 项失败")
        return 1
    print("\n完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
