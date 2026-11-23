"""验证图片处理辅助函数，不连接飞书、Nacos 或 MinIO。"""

import asyncio
from typing import Any

import pytest

from deal_pilot.config.nacos.nacos_schemas import RagConfig, RagImageParser
from deal_pilot.rag.structure.feishu_doc import image_parser
from deal_pilot.rag.structure.feishu_doc.block_node import BlockNode
from deal_pilot.rag.structure.feishu_doc.block_type import BlockType


def node(
    block_id: str,
    content: str = "",
    block_type: BlockType = BlockType.TEXT,
) -> BlockNode:
    return BlockNode(block_id=block_id, block_type=block_type, content=content)


def build_siblings() -> tuple[BlockNode, dict[str, BlockNode]]:
    """构造标题内的文本、图片和标题外的兄弟节点。"""
    root = node("root", block_type=BlockType.PAGE)
    heading = node("heading", "第一章", BlockType.HEADING_1)
    nodes = {
        "text1": node("text1", "a"),
        "image1": node("image1", "img1", BlockType.IMAGE),
        "text2": node("text2", ""),
        "image2": node("image2", "img2", BlockType.IMAGE),
        "text3": node("text3", "c"),
        "image3": node("image3", "img3", BlockType.IMAGE),
        "text4": node("text4", "d"),
    }
    root.append(heading)
    for child in nodes.values():
        heading.append(child)
    root.append(node("tail", "尾"))
    return root, nodes


def patch_config(monkeypatch: pytest.MonkeyPatch, sibling_count: int = 2) -> None:
    async def get_config(config_name: str, schema: type[RagConfig]) -> RagConfig:
        assert config_name == "rag_config"
        assert schema is RagConfig
        return RagConfig(
            image_parser=RagImageParser(
                bucket_name="feishu-images", sibling_count=sibling_count
            )
        )

    monkeypatch.setattr(image_parser, "get_config", get_config)


def test_generate_dataurl_encodes_mime_and_base64() -> None:
    assert image_parser._generate_dataurl(b"\x89PNG", "image/png") == (
        "data:image/png;base64,iVBORw=="
    )


def test_generate_dataurl_normalizes_content_type_parameters() -> None:
    assert image_parser._generate_dataurl(
        b"a", "IMAGE/JPEG; charset=binary"
    ) == "data:image/jpeg;base64,YQ=="


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (b"a", ""),
        (b"a", "   "),
        (b"a", " ; charset=binary"),
        ("text", "image/png"),
    ],
)
def test_generate_dataurl_rejects_invalid_arguments(data: Any, mime: str) -> None:
    with pytest.raises(ValueError):
        image_parser._generate_dataurl(data, mime)


def test_upload_minio_uses_private_bucket_and_mime_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMinio:
        def __init__(self) -> None:
            self.created: list[tuple[str, bool]] = []
            self.uploaded: list[dict[str, Any]] = []

        async def create_bucket(self, bucket_name: str, allow_public_read: bool = True) -> None:
            self.created.append((bucket_name, allow_public_read))

        async def upload_bytes(self, **kwargs: Any) -> None:
            self.uploaded.append(kwargs)

    client = FakeMinio()
    patch_config(monkeypatch)
    monkeypatch.setattr(image_parser, "_minio_client", lambda: client)

    uploaded = asyncio.run(
        image_parser._upload_minio(b"\x89PNG", "IMAGE/PNG; charset=binary", "block-1")
    )

    assert (uploaded.bucket_name, uploaded.filename) == ("feishu-images", "block-1.png")
    assert client.created == [("feishu-images", False)]
    assert client.uploaded == [{
        "bucket_name": "feishu-images",
        "filename": "block-1.png",
        "data": b"\x89PNG",
        "mime": "image/png",
    }]


@pytest.mark.parametrize(
    ("mime", "filename"),
    [
        ("image/png", "b.png"),
        ("image/jpeg", "b.jpg"),
        ("image/gif", "b.gif"),
        ("image/webp", "b.webp"),
    ],
)
def test_upload_minio_maps_common_image_mime(
    monkeypatch: pytest.MonkeyPatch, mime: str, filename: str
) -> None:
    class FakeMinio:
        async def create_bucket(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        async def upload_bytes(self, **_kwargs: Any) -> None:
            return None

    patch_config(monkeypatch)
    monkeypatch.setattr(image_parser, "_minio_client", FakeMinio)
    uploaded = asyncio.run(image_parser._upload_minio(b"x", mime, "b"))
    assert uploaded.filename == filename


@pytest.mark.parametrize(
    ("mime", "block_id"),
    [("application/pdf", "b"), ("image/png", ""), ("image/png", "a/b"), ("image/png", "..")],
)
def test_upload_minio_rejects_unsupported_mime_or_block_id(
    monkeypatch: pytest.MonkeyPatch, mime: str, block_id: str
) -> None:
    patch_config(monkeypatch)

    with pytest.raises(ValueError):
        asyncio.run(image_parser._upload_minio(b"x", mime, block_id))


def test_attachment_info_collects_trace_and_siblings(monkeypatch: pytest.MonkeyPatch) -> None:
    _root, nodes = build_siblings()
    patch_config(monkeypatch, sibling_count=2)
    nodes["image1"].content = "已处理图片1"

    info = asyncio.run(image_parser._attachment_info(nodes["image2"]))
    assert info.parent_trace == ["第一章"]
    # 不跳过图片，且使用上一张图片处理后的内容；结果按文档顺序。
    assert info.prevs == ["已处理图片1", ""]
    # image3 被跳过且不计入数量，补足两个文本节点。
    assert info.nexts == ["c", "d"]


def test_attachment_info_uses_actual_count_when_not_enough_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _root, nodes = build_siblings()
    patch_config(monkeypatch, sibling_count=10)

    info = asyncio.run(image_parser._attachment_info(nodes["image3"]))
    assert info.prevs == ["a", "img1", "", "img2", "c"]
    # 不跨父节点读取标题外的 tail。
    assert info.nexts == ["d"]


def test_attachment_info_zero_siblings(monkeypatch: pytest.MonkeyPatch) -> None:
    _root, nodes = build_siblings()
    patch_config(monkeypatch, sibling_count=0)

    info = asyncio.run(image_parser._attachment_info(nodes["image2"]))
    assert info.parent_trace == ["第一章"]
    assert info.prevs == []
    assert info.nexts == []


def test_attachment_info_filters_empty_parent_content(monkeypatch: pytest.MonkeyPatch) -> None:
    root = node("root", block_type=BlockType.PAGE)
    heading = node("heading", "第一章", BlockType.HEADING_1)
    empty = node("empty", "", BlockType.HEADING_2)
    image = node("image", "token", BlockType.IMAGE)
    root.append(heading)
    heading.append(empty)
    empty.append(image)
    patch_config(monkeypatch, sibling_count=2)

    info = asyncio.run(image_parser._attachment_info(image))
    assert info.parent_trace == ["第一章"]
    assert info.prevs == []
    assert info.nexts == []


def test_attachment_info_requires_no_media_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    _root, nodes = build_siblings()
    patch_config(monkeypatch)
    info = asyncio.run(image_parser._attachment_info(nodes["image2"]))
    assert info.prevs == ["img1", ""]
