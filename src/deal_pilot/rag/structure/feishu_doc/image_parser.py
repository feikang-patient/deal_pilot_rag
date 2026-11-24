"""飞书文档图片的私有存储与上下文提取辅助能力。

`parse_image_block` 仍是占位实现；本模块先提供后续图片解析所需的基础函数。
"""

import base64
from dataclasses import dataclass

from deal_pilot.config.minio_settings import minio_settings
from deal_pilot.config.nacos.nacos_config import get_config
from deal_pilot.config.nacos.nacos_schemas import RagConfig
from deal_pilot.rag.structure.feishu_doc.block_node import BlockNode
from deal_pilot.rag.structure.feishu_doc.block_type import BlockType
from deal_pilot.utils.minio import MinIO

from langchain.agents import create_agent
from deal_pilot.utils.create_model import create_model
from deal_pilot.config.nacos.nacos_prompt import get_prompt
from langchain.messages import HumanMessage
from deal_pilot.feishu.api import MediaAPI

# 文档图片常见 MIME 到对象键后缀的固定映射；不依赖操作系统的 MIME 配置，
# 避免同一 MIME 在不同环境生成不同文件名。未列出的 MIME 直接报错。
_MIME_EXTENSIONS: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
    "image/tiff": ".tiff",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/avif": ".avif",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


@dataclass(slots=True)
class UploadedImage:
    """已上传到 MinIO 的图片对象位置。"""

    bucket_name: str
    filename: str


@dataclass(slots=True)
class AttachmentInfo:
    """图片的父级路径和前后兄弟内容，用于后续图片解析提示词。"""

    parent_trace: list[str]
    prevs: list[str]
    nexts: list[str]


def _normalize_mime(mime: str) -> str:
    """去掉 Content-Type 参数并转为小写；缺少类型或为空时报错。"""
    if not isinstance(mime, str):
        raise ValueError("mime 必须是非空字符串")
    normalized = mime.split(";", 1)[0].strip().lower()
    if not normalized:
        raise ValueError("mime 必须包含具体的类型")
    return normalized


def _generate_dataurl(data: bytes, mime: str) -> str:
    """返回包含 MIME 类型及 Base64 图片数据的 Data URL。"""
    if not isinstance(data, bytes):
        raise ValueError("data 必须是 bytes")
    mime = _normalize_mime(mime)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _minio_client() -> MinIO:
    """按环境配置创建 MinIO 客户端；构造阶段不连接服务端。"""
    return MinIO(
        endpoint=minio_settings.endpoint,
        access_key=minio_settings.access_key,
        secret_key=minio_settings.secret_key,
        secure=minio_settings.secure,
    )


def _image_filename(block_id: str, mime: str) -> str:
    """按 block_id 和 MIME 后缀生成桶根目录下的对象键。"""
    if not isinstance(block_id, str) or not block_id.strip():
        raise ValueError("block_id 必须是非空字符串")
    if block_id in {".", ".."} or "/" in block_id or "\\" in block_id:
        raise ValueError("block_id 不能包含路径分隔符")
    extension = _MIME_EXTENSIONS.get(mime)
    if extension is None:
        raise ValueError(f"不支持的图片 MIME：{mime}")
    return f"{block_id}{extension}"


async def _upload_minio(data: bytes, mime: str, block_id: str) -> UploadedImage:
    """将图片上传到 Nacos 配置的图片桶根目录，并返回桶名和对象键。

    桶名来自 rag_config.image_parser.bucket_name；建桶时禁用公共读，
    不新增匿名读取策略。对象键由 block_id 与 MIME 对应后缀组成，
    Content-Type 使用传入的 mime。同名对象覆盖，不创建子目录。
    """
    if not isinstance(data, bytes):
        raise ValueError("data 必须是 bytes")
    mime = _normalize_mime(mime)
    filename = _image_filename(block_id, mime)
    config = await get_config("rag_config", RagConfig)
    bucket_name = config.image_parser.bucket_name
    client = _minio_client()
    await client.create_bucket(bucket_name, allow_public_read=False)
    await client.upload_bytes(
        bucket_name=bucket_name,
        filename=filename,
        data=data,
        mime=mime,
    )
    return UploadedImage(bucket_name=bucket_name, filename=filename)


async def _attachment_info(image_block: BlockNode) -> AttachmentInfo:
    """提取图片的父级路径与前后兄弟内容。

    parent_trace 按根节点到直接父节点的顺序收集非空内容，不包含图片自身。
    prevs 从直接前一个兄弟开始向前取 n 个兄弟，不跳过图片节点，
    因此可用到前一张图片处理后的内容；结果按文档顺序返回。
    nexts 从直接后一个兄弟开始向后查找 n 个非图片兄弟，跳过图片且
    不计入数量；结果按文档顺序返回。
    n 来自 rag_config.image_parser.sibling_count。前后查找均不跨父节点；
    可用节点不足 n 个时返回实际内容，没有时返回空列表。
    """
    config = await get_config("rag_config", RagConfig)
    count = config.image_parser.sibling_count

    parent_trace: list[str] = []
    parent = image_block.parent
    while parent is not None:
        if parent.content:
            parent_trace.append(parent.content)
        parent = parent.parent
    parent_trace.reverse()

    previous: list[str] = []
    sibling = image_block.prev
    while sibling is not None and len(previous) < count:
        if sibling.parent is not image_block.parent:
            break
        previous.append(sibling.content)
        sibling = sibling.prev
    previous.reverse()

    following: list[str] = []
    sibling = image_block.next
    while sibling is not None and len(following) < count:
        if sibling.parent is not image_block.parent:
            break
        if sibling.block_type != BlockType.IMAGE:
            following.append(sibling.content)
        sibling = sibling.next

    return AttachmentInfo(parent_trace=parent_trace, prevs=previous, nexts=following)


async def parse_image_block(image_block: BlockNode) -> None:

    rag_config = await get_config("rag_config", RagConfig)
    model = create_model(rag_config.image_parser.vl_model_config)
    system_prompt = await get_prompt(rag_config.image_parser.image_parser_prompt)
    agent = create_agent(model=model, system_prompt=system_prompt)
    attachment = await _attachment_info(image_block)
    attachment.parent_trace
    parent_trace = "/".join(attachment.parent_trace)
    prevs = "\n\n".join(attachment.prevs)
    nexts = "\n\n".join(attachment.nexts)
    text_content = f"""
parent_trace:
{parent_trace}

prevs:
{prevs}

nexts:
{nexts}
"""
    img_api = MediaAPI()
    img = await img_api.download_image(image_block.content)
    minio_result = await _upload_minio(img.data, img.mime, image_block.block_id)
    dataurl = _generate_dataurl(img.data, img.mime)

    result = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(
                    [
                        {"type": "text", "text": text_content},
                        {"type": "image_url", "image_url": {"url": dataurl}},
                    ]
                )
            ]
        }
    )

    image_block.content = result["messages"][-1].text
    image_block.payload = {
        "bucket_name": minio_result.bucket_name,
        "bucket_filename": minio_result.filename,
    }
