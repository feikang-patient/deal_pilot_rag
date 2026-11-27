#!/usr/bin/env python3
"""把本地 Markdown 导入飞书知识库；一个子文件夹对应一个知识空间。

默认只做本地预演；加 --write 才会上传并创建文档。重复执行按标题跳过，
失败逐条报告，不静默跳过。
"""

import argparse
import asyncio
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

from deal_pilot.feishu.api import DocxAPI, PublishAPI, WikiAPI  # noqa: E402
from deal_pilot.feishu.api.errors import FeishuAPIError  # noqa: E402

DEFAULT_SOURCE = PROJECT_ROOT / "resource" / "企业知识库"
IMAGE_FOLDER = "图片素材"
IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
MAX_IMAGE_WIDTH = 640


@dataclass
class ImageRef:
    marker: str
    path: Path
    mime: str
    width: int
    height: int


@dataclass
class Entry:
    space_name: str
    path: Path
    title: str
    markdown: bytes
    images: list[ImageRef] = field(default_factory=list)


def normalize(name: str) -> str:
    """去掉子文件夹编号，用于匹配知识空间名称。"""
    return re.sub(r"^\s*\d+\s*[_\-、.]?\s*", "", name).strip()


def png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def load_image(marker: str, path: Path) -> ImageRef:
    data = path.read_bytes()
    size = png_size(data)
    if size is None:
        raise ValueError(f"仅支持 PNG 图片：{path}")
    width, height = size
    if width > MAX_IMAGE_WIDTH:
        height = max(1, round(height * MAX_IMAGE_WIDTH / width))
        width = MAX_IMAGE_WIDTH
    suffix = path.suffix.lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        suffix.lstrip("."), "application/octet-stream"
    )
    return ImageRef(marker, path, mime, width, height)


def render_markdown(text: str, base_dir: Path) -> tuple[bytes, list[ImageRef]]:
    """把图片引用替换为说明文字，返回 Markdown 字节和图片列表。"""
    images: list[ImageRef] = []

    def replace(match: re.Match[str]) -> str:
        alt = match.group(1).strip() or Path(match.group(2)).stem
        path = (base_dir / match.group(2)).resolve()
        images.append(load_image(f"（图片：{alt}）", path))
        return f"（图片：{alt}）"

    return IMAGE_PATTERN.sub(replace, text).encode("utf-8"), images


def collect(source: Path) -> list[Entry]:
    entries: list[Entry] = []
    for folder in sorted(p for p in source.iterdir() if p.is_dir()):
        if folder.name == IMAGE_FOLDER:
            continue
        for path in sorted(folder.glob("*.md")):
            content, images = render_markdown(
                path.read_text(encoding="utf-8"), path.parent
            )
            entries.append(Entry(folder.name, path, path.stem, content, images))
    return entries


async def verify_blocks(docs: DocxAPI, token: str, attempts: int = 3) -> int | None:
    """移动完成后立即读块可能短暂失败；重试仍失败时返回 None，不误报导入失败。"""
    for attempt in range(attempts):
        try:
            return len(await docs.list_blocks(token))
        except Exception:  # noqa: BLE001 校验失败只告警，节点已创建
            if attempt + 1 < attempts:
                await asyncio.sleep(1.5)
    return None


def block_text(block) -> str:
    parts: list[str] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            run = value.get("text_run")
            if isinstance(run, dict) and isinstance(run.get("content"), str):
                parts.append(run["content"])
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(block.raw)
    return "".join(parts)


async def insert_images(
    docs: DocxAPI, publish: PublishAPI, token: str, images: list[ImageRef]
) -> tuple[int, list[str]]:
    """把占位段落替换为真实图片；找不到占位说明时视为已处理。"""
    inserted, failures = 0, []
    for image in images:
        try:
            blocks = await docs.list_blocks(token)
            page = next((block for block in blocks if block.block_type == 1), None)
            if page is None:
                failures.append(f"{image.path.name}: 找不到页面根块")
                break
            by_id = {block.block_id: block for block in blocks}
            index = next(
                (i for i, child_id in enumerate(page.children)
                 if child_id in by_id and image.marker in block_text(by_id[child_id])),
                None,
            )
            if index is None:
                continue
            await publish.insert_image(
                document_id=token,
                parent_block_id=page.block_id,
                index=index + 1,
                file_name=image.path.name,
                content=image.path.read_bytes(),
                width=image.width,
                height=image.height,
            )
            await publish.delete_block(
                document_id=token,
                parent_block_id=page.block_id,
                index=index,
            )
            inserted += 1
        except Exception as error:  # noqa: BLE001 单张失败不阻断后续
            failures.append(f"{image.path.name}: {type(error).__name__}: {error}")
    return inserted, failures


async def run(entries: list[Entry], *, write: bool, with_images: bool) -> int:
    wiki, docs, publish = WikiAPI(), DocxAPI(), PublishAPI()
    spaces = {normalize(space.name): space for space in await wiki.list_spaces()}
    print(f"可见知识空间：{', '.join(sorted(s.name for s in spaces.values())) or '（无）'}")

    failures = 0
    for entry in entries:
        space = spaces.get(normalize(entry.space_name))
        if space is None:
            print(f"[缺空间] {entry.space_name} / {entry.title}")
            failures += 1
            continue
        try:
            nodes = await wiki.list_nodes(space.space_id)
        except FeishuAPIError as error:
            print(f"[无权访问] {space.name}：code={error.code}，请把应用加为知识库成员")
            failures += 1
            continue
        existing = {node.title: node for node in nodes}
        if entry.title in existing:
            node = existing[entry.title]
            if write and with_images:
                inserted, image_failures = await insert_images(docs, publish, node.obj_token, entry.images)
                failures += len(image_failures)
                print(f"[补图] {space.name} / {entry.title}：新增 {inserted} 张"
                      + (f"，失败 {len(image_failures)} 张" if image_failures else ""))
                for failure in image_failures:
                    print(f"    - {failure}")
            else:
                print(f"[跳过] {space.name} / {entry.title}（已存在）")
            continue
        if not write:
            print(f"[预演] {space.name} / {entry.title}（{len(entry.markdown)} 字节，图片 {len(entry.images)} 张）")
            continue
        try:
            token = await publish.import_markdown(
                file_name=entry.path.name, content=entry.markdown, title=entry.title
            )
            node_token = await publish.attach_docx_to_wiki(
                space_id=space.space_id, docx_token=token
            )
        except Exception as error:  # noqa: BLE001 逐条记录，最后统一失败退出
            failures += 1
            print(f"[失败] {space.name} / {entry.title}：{type(error).__name__}: {error}")
            continue
        blocks = await verify_blocks(docs, token)
        if blocks is None:
            print(f"[已建节点] {space.name} / {entry.title} -> {node_token}（块数校验未完成）")
        else:
            print(f"[完成] {space.name} / {entry.title} -> {node_token}（{blocks} 块）")
        if with_images:
            inserted, image_failures = await insert_images(docs, publish, token, entry.images)
            failures += len(image_failures)
            print(f"    图片：新增 {inserted} 张" + (f"，失败 {len(image_failures)} 张" if image_failures else ""))
            for failure in image_failures:
                print(f"    - {failure}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--only", default="", help="只处理路径包含该子串的文档")
    parser.add_argument("--skip", default="", help="跳过路径包含该子串的文档")
    parser.add_argument("--write", action="store_true", help="真正上传并创建飞书文档")
    parser.add_argument("--with-images", action="store_true", help="上传真实图片并替换占位段落")
    args = parser.parse_args()

    entries = collect(args.source)
    if args.only:
        entries = [entry for entry in entries if args.only in str(entry.path)]
    if args.skip:
        entries = [entry for entry in entries if args.skip not in str(entry.path)]
    if not entries:
        print("没有找到可导入的 Markdown")
        return 1
    mode = "写入" if args.write else "预演"
    print(f"模式：{mode}，共 {len(entries)} 篇，来源 {args.source}")
    failures = asyncio.run(run(entries, write=args.write, with_images=args.with_images))
    if failures:
        print(f"\n有 {failures} 项未完成")
        return 1
    print("\n全部完成" if args.write else "\n预演结束，未写入飞书")
    return 0


if __name__ == "__main__":
    sys.exit(main())
