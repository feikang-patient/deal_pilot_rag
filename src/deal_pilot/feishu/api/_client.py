"""官方 SDK 的调用边界、响应校验及全量分页。"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache

import lark_oapi as lark
from lark_oapi.api.docx.v1.version import V1 as DocxV1
from lark_oapi.api.drive.v1 import DownloadMediaResponse
from lark_oapi.api.drive.v1.version import V1 as DriveV1
from lark_oapi.api.wiki.v2.version import V2 as WikiV2
from lark_oapi.core.exception import ObtainAccessTokenException, UnmarshalException
from lark_oapi.core.model import BaseResponse, RawResponse
from pydantic import JsonValue

from deal_pilot.config.feishu_settings import feishu_settings
from deal_pilot.feishu.api.errors import (
    FeishuAPIError,
    FeishuAuthenticationError,
    FeishuConfigurationError,
    FeishuHTTPError,
    FeishuResponseError,
)


@lru_cache(maxsize=1)
def _build_client(app_id: str, app_secret: str) -> lark.Client:
    return (lark.Client.builder().app_id(app_id).app_secret(app_secret)
            .timeout(30).log_level(lark.LogLevel.ERROR).build())


def _client() -> lark.Client:
    app_id, app_secret = feishu_settings.app_id, feishu_settings.app_secret
    if not app_id.strip() or not app_secret.strip():
        raise FeishuConfigurationError("请配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
    return _build_client(app_id, app_secret)


def wiki_client() -> WikiV2:
    client = _client()
    assert client.wiki is not None  # 官方 builder 会初始化所有服务。
    return client.wiki.v2


def docx_client() -> DocxV1:
    client = _client()
    assert client.docx is not None
    return client.docx.v1


def media_client() -> DriveV1:
    client = _client()
    assert client.drive is not None
    return client.drive.v1


def resource_id(value: str, name: str) -> str:
    """校验单个资源标识；路径编码由 SDK 处理。"""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} 必须是非空资源 ID/token")
    if value in {".", ".."} or any(c.isspace() or c in "/\\?#" for c in value):
        raise ValueError(f"{name} 应传资源 ID/token，不能传链接或路径")
    return value


def object_value(value: JsonValue, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise FeishuResponseError(f"飞书响应缺少有效的 {name} 对象")
    return value


def _checked_raw(response: BaseResponse) -> RawResponse:
    """校验 SDK 响应状态；返回可读取响应头和内容的原始响应。"""
    raw = response.raw
    if raw is None or type(raw.status_code) is not int:
        raise FeishuResponseError("飞书 SDK 响应缺少有效的 HTTP 状态")
    log_id = next((v for k, v in raw.headers.items() if k.lower() == "x-tt-logid"), None)
    if raw.status_code >= 500:
        raise FeishuHTTPError(raw.status_code, log_id)
    if type(response.code) is not int:
        raise FeishuResponseError("飞书 SDK 响应缺少有效的 code")
    if not response.success():
        raise FeishuAPIError(response.code, raw.status_code, log_id)
    if not 200 <= raw.status_code < 300:
        raise FeishuHTTPError(raw.status_code, log_id)
    return raw


async def sdk_call(operation: Callable[[], BaseResponse]) -> dict[str, JsonValue]:
    """完整 SDK 调用在线程中执行，包括同步鉴权；Token 获取与缓存交给 SDK。"""
    try:
        response = await asyncio.to_thread(operation)
    except ObtainAccessTokenException as error:
        # SDK 的原始异常包含服务端 msg，不直接输出，避免带出凭据。
        raise FeishuAuthenticationError(error.code) from None
    except (json.JSONDecodeError, UnmarshalException):
        raise FeishuResponseError("飞书 SDK 无法解析服务响应") from None
    raw = _checked_raw(response)
    if raw.content is None:
        raise FeishuResponseError("飞书 SDK 响应缺少原始内容")
    # SDK 模型会忽略未声明字段；从 raw 保留完整块数据及未来新增字段。
    payload = object_value(json.loads(raw.content), "响应")
    return object_value(payload.get("data"), "data")


async def sdk_download(
    operation: Callable[[], DownloadMediaResponse],
) -> tuple[str, bytes]:
    """线程中执行二进制下载，返回规范化的 Content-Type 和原始字节。"""
    try:
        response = await asyncio.to_thread(operation)
    except ObtainAccessTokenException as error:
        raise FeishuAuthenticationError(error.code) from None
    except (json.JSONDecodeError, UnmarshalException):
        raise FeishuResponseError("飞书 SDK 无法解析服务响应") from None
    raw = _checked_raw(response)
    content_type = next(
        (v for k, v in raw.headers.items() if k.lower() == "content-type"), None
    )
    if not isinstance(content_type, str) or not content_type.split(";", 1)[0].strip():
        raise FeishuResponseError("飞书响应缺少有效的 Content-Type")
    mime = content_type.split(";", 1)[0].strip().lower()
    if response.file is None:
        raise FeishuResponseError("飞书响应缺少下载内容")
    data = response.file.read()
    if not isinstance(data, bytes):
        raise FeishuResponseError("飞书响应下载内容不是字节")
    return mime, data


async def iter_items(
    fetch_page: Callable[[str | None], Awaitable[dict[str, JsonValue]]],
) -> AsyncIterator[dict[str, JsonValue]]:
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        data = await fetch_page(page_token)
        items = data.get("items")
        if items is None:
            items = []
        if not isinstance(items, list) or type(data.get("has_more")) is not bool:
            raise FeishuResponseError("飞书分页响应缺少有效的 items/has_more")
        for item in items:
            yield object_value(item, "items[]")
        if not data["has_more"]:
            return
        token = data.get("page_token")
        if not isinstance(token, str) or not token or token in seen_tokens:
            raise FeishuResponseError("飞书分页游标缺失或重复，无法读取完整列表")
        seen_tokens.add(token)
        page_token = token
