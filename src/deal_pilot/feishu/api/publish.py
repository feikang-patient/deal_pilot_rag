"""本地 Markdown 发布到飞书知识库：导入、图片与节点挂载。"""

import asyncio
import io
import json
from typing import IO, Final, cast

import httpx
from lark_oapi.api.docx.v1 import (
    BatchDeleteDocumentBlockChildrenRequest,
    BatchDeleteDocumentBlockChildrenRequestBody,
    Block,
    CreateDocumentBlockChildrenRequest,
    CreateDocumentBlockChildrenRequestBody,
    Image,
    PatchDocumentBlockRequest,
    ReplaceImageRequest,
    UpdateBlockRequest,
)
from lark_oapi.api.drive.v1 import (
    CreateImportTaskRequest,
    GetImportTaskRequest,
    ImportTask,
    ImportTaskMountPoint,
    UploadAllMediaRequest,
    UploadAllMediaRequestBody,
)
from lark_oapi.api.wiki.v2 import (
    GetTaskRequest,
    MoveDocsToWikiSpaceNodeRequest,
    MoveDocsToWikiSpaceNodeRequestBody,
)
from lark_oapi.core.token import TokenManager
from pydantic import JsonValue

from deal_pilot.feishu.api._client import (
    _client,
    docx_client,
    drive_client,
    resource_id,
    sdk_call,
    wiki_client,
)
from deal_pilot.feishu.api.errors import FeishuAPIError, FeishuImportError, FeishuResponseError

_OPEN_API: Final = "https://open.feishu.cn/open-apis"

_POLL_INTERVAL_SECONDS: Final = 1.0
_IMPORT_TIMEOUT_SECONDS: Final = 180.0
_MOVE_TIMEOUT_SECONDS: Final = 120.0
_DELETE_TIMEOUT_SECONDS: Final = 120.0
_IMPORT_PENDING: Final = frozenset({1, 2})
_DELETE_ALREADY_GONE: Final = 131012
_IMAGE_BLOCK_TYPE: Final = 27


class PublishAPI:
    """以应用身份把 Markdown 导入为 Docx，并挂载到知识空间。"""

    async def import_markdown(
        self,
        *,
        file_name: str,
        content: bytes,
        title: str,
        timeout_seconds: float = _IMPORT_TIMEOUT_SECONDS,
    ) -> str:
        """导入单个 Markdown 文件，返回新文档的 docx token。

        file_name 必须与内容同为 Markdown；title 为空或导入失败时抛错，
        不把失败任务当成空文档。
        """
        if not file_name.lower().endswith(".md") or file_name != file_name.strip():
            raise ValueError("file_name 必须是单个 .md 文件名")
        if not title.strip() or title != title.strip():
            raise ValueError("title 必须是非空标题且首尾无空白")
        if not content:
            raise ValueError("Markdown 内容不能为空")
        file_token = await self._upload_markdown(file_name, content)
        ticket = await self._create_import_task(file_token, title)
        return await self._wait_import_task(ticket, timeout_seconds)

    async def attach_docx_to_wiki(
        self,
        *,
        space_id: str,
        docx_token: str,
        parent_wiki_token: str | None = None,
        timeout_seconds: float = _MOVE_TIMEOUT_SECONDS,
    ) -> str:
        """把已有 Docx 移动为知识空间节点，返回 wiki node_token。

        不传 parent_wiki_token 时挂为空间一级节点；异步任务失败时抛错。
        """
        space_id = resource_id(space_id, "space_id")
        docx_token = resource_id(docx_token, "docx_token")
        if parent_wiki_token is not None:
            parent_wiki_token = resource_id(parent_wiki_token, "parent_wiki_token")
        body = (MoveDocsToWikiSpaceNodeRequestBody.builder()
                .obj_type("docx").obj_token(docx_token))
        if parent_wiki_token is not None:
            body = body.parent_wiki_token(parent_wiki_token)
        request = (MoveDocsToWikiSpaceNodeRequest.builder()
                   .space_id(space_id).request_body(body.build()).build())
        api = wiki_client()
        data = await sdk_call(lambda: api.space_node.move_docs_to_wiki(request))
        wiki_token = data.get("wiki_token")
        if isinstance(wiki_token, str) and wiki_token:
            return wiki_token
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise FeishuResponseError("飞书移动响应缺少 node_token 或 task_id")
        return await self._wait_move_task(task_id, timeout_seconds)

    async def insert_image(
        self,
        *,
        document_id: str,
        parent_block_id: str,
        index: int,
        file_name: str,
        content: bytes,
        width: int,
        height: int,
    ) -> str:
        """在父块第 index 位插入图片块，上传素材并绑定，返回图片块 ID。"""
        document_id = resource_id(document_id, "document_id")
        parent_block_id = resource_id(parent_block_id, "parent_block_id")
        if type(index) is not int or index < 0:
            raise ValueError("index 必须是非负整数")
        if not content or width <= 0 or height <= 0:
            raise ValueError("图片内容与宽高必须有效")
        block = (Block.builder().block_type(_IMAGE_BLOCK_TYPE)
                 .image(Image.builder().build()).build())
        body = (CreateDocumentBlockChildrenRequestBody.builder()
                .children([block]).index(index).build())
        request = (CreateDocumentBlockChildrenRequest.builder()
                   .document_id(document_id).block_id(parent_block_id)
                   .request_body(body).build())
        api = docx_client()
        data = await sdk_call(lambda: api.document_block_children.create(request))
        children = data.get("children")
        if not isinstance(children, list) or not children:
            raise FeishuResponseError("飞书创建图片块响应缺少 children")
        block_id = _object(children[0], "children[0]").get("block_id")
        if not isinstance(block_id, str) or not block_id:
            raise FeishuResponseError("飞书创建图片块响应缺少 block_id")
        file_token = await self._upload_bytes(
            file_name=file_name,
            content=content,
            parent_type="docx_image",
            parent_node=block_id,
            content_type=_image_mime(file_name),
        )
        patch_body = (UpdateBlockRequest.builder()
                      .replace_image(ReplaceImageRequest.builder()
                                     .token(file_token).width(width).height(height).build())
                      .build())
        patch = (PatchDocumentBlockRequest.builder()
                 .document_id(document_id).block_id(block_id)
                 .request_body(patch_body).build())
        await sdk_call(lambda: api.document_block.patch(patch))
        return block_id

    async def delete_block(
        self, *, document_id: str, parent_block_id: str, index: int
    ) -> None:
        """删除父块下第 index 个直接子块；用于清理图片占位段落。"""
        document_id = resource_id(document_id, "document_id")
        parent_block_id = resource_id(parent_block_id, "parent_block_id")
        if type(index) is not int or index < 0:
            raise ValueError("index 必须是非负整数")
        body = (BatchDeleteDocumentBlockChildrenRequestBody.builder()
                .start_index(index).end_index(index + 1).build())
        request = (BatchDeleteDocumentBlockChildrenRequest.builder()
                   .document_id(document_id).block_id(parent_block_id)
                   .request_body(body).build())
        await sdk_call(lambda: docx_client().document_block_children.batch_delete(request))

    async def delete_wiki_node(
        self,
        *,
        space_id: str,
        node_token: str,
        timeout_seconds: float = _DELETE_TIMEOUT_SECONDS,
    ) -> None:
        """删除知识空间节点；节点已不存在视为成功。

        SDK 1.7.4 没有该接口，这里按官方文档直接请求并复用 SDK 的 tenant token。
        """
        space_id = resource_id(space_id, "space_id")
        node_token = resource_id(node_token, "node_token")
        config = getattr(_client(), "_config")
        token: str = await asyncio.to_thread(TokenManager.get_self_tenant_token, config)
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{_OPEN_API}/wiki/v2/spaces/{space_id}/nodes/{node_token}"
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                "DELETE",
                url,
                headers=headers,
                json={"obj_type": "wiki", "include_children": True},
            )
            data = _json_response(response)
            if data.get("code") == _DELETE_ALREADY_GONE:
                return
            _raise_on_error(data, response)
            task_id = _object(data.get("data"), "data").get("task_id")
            if not isinstance(task_id, str) or not task_id:
                return
            await self._wait_delete_task(client, headers, task_id, timeout_seconds)

    async def _wait_delete_task(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        task_id: str,
        timeout_seconds: float,
    ) -> None:
        url = f"{_OPEN_API}/wiki/v2/tasks/{task_id}"
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            response = await client.get(
                url, headers=headers, params={"task_type": "delete_node"}
            )
            data = _json_response(response)
            _raise_on_error(data, response)
            task = _object(data.get("data"), "data").get("task")
            simple = _object(_object(task, "task").get("simple_task_result"), "simple_task_result")
            status = simple.get("status")
            if not isinstance(status, str):
                raise FeishuResponseError("飞书删除任务缺少有效的 status")
            if status.lower() == "success":
                return
            if status.lower() in {"failure", "failed"}:
                raise FeishuImportError(-1, f"删除任务失败：{status}")
            if asyncio.get_running_loop().time() >= deadline:
                raise FeishuImportError(-1, "删除任务超时")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def _upload_markdown(self, file_name: str, content: bytes) -> str:
        return await self._upload_bytes(
            file_name=file_name,
            content=content,
            parent_type="ccm_import_open",
            extra=json.dumps({"obj_type": "docx", "file_extension": "md"}),
            content_type="text/markdown",
        )

    async def _upload_bytes(
        self,
        *,
        file_name: str,
        content: bytes,
        parent_type: str,
        parent_node: str = "",
        extra: str | None = None,
        content_type: str = "application/octet-stream",
    ) -> str:
        # 同步资源方法把 body.file 转成 multipart；不能改用 request.files，
        # 同步传输层只读取 body，会退化成 JSON 请求并导致 size 校验失败。
        builder = (UploadAllMediaRequestBody.builder()
                   .file_name(file_name)
                   .parent_type(parent_type)
                   .size(len(content))
                   # SDK 的 Builder 把 file 标注为 IO，MultipartEncoder 也接受
                   # (filename, fileobj, content_type) 元组；只在边界处收窄类型。
                   .file(cast(IO[bytes], (file_name, io.BytesIO(content), content_type))))
        if parent_node:
            builder = builder.parent_node(parent_node)
        if extra is not None:
            builder = builder.extra(extra)
        body = builder.build()
        request = UploadAllMediaRequest.builder().request_body(body).build()
        api = drive_client()
        data = await sdk_call(lambda: api.media.upload_all(request))
        file_token = data.get("file_token")
        if not isinstance(file_token, str) or not file_token:
            raise FeishuResponseError("飞书上传响应缺少 file_token")
        return file_token

    async def _create_import_task(self, file_token: str, title: str) -> str:
        body = (ImportTask.builder()
                .file_extension("md")
                .file_token(file_token)
                .type("docx")
                .file_name(title)
                .point(ImportTaskMountPoint.builder()
                       .mount_type(1)
                       .mount_key("")
                       .build())
                .build())
        request = CreateImportTaskRequest.builder().request_body(body).build()
        api = drive_client()
        data = await sdk_call(lambda: api.import_task.create(request))
        ticket = data.get("ticket")
        if not isinstance(ticket, str) or not ticket:
            raise FeishuResponseError("飞书导入响应缺少 ticket")
        return ticket

    async def _wait_import_task(self, ticket: str, timeout_seconds: float) -> str:
        api = drive_client()
        request = GetImportTaskRequest.builder().ticket(ticket).build()
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            data = await sdk_call(lambda: api.import_task.get(request))
            result = _object(data.get("result"), "result")
            status = result.get("job_status")
            if type(status) is not int:
                raise FeishuResponseError("飞书导入结果缺少有效的 job_status")
            if status == 0:
                token = result.get("token")
                if not isinstance(token, str) or not token:
                    raise FeishuResponseError("飞书导入结果缺少 docx token")
                return token
            if status not in _IMPORT_PENDING:
                raise FeishuImportError(status, str(result.get("job_error_msg") or ""))
            if asyncio.get_running_loop().time() >= deadline:
                raise FeishuImportError(status, "导入任务超时")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def _wait_move_task(self, task_id: str, timeout_seconds: float) -> str:
        api = wiki_client()
        request = GetTaskRequest.builder().task_type("move").task_id(task_id).build()
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            data = await sdk_call(lambda: api.task.get(request))
            task = _object(data.get("task"), "task")
            results = task.get("move_result")
            if isinstance(results, list) and results:
                result = _object(results[0], "move_result[0]")
                status = result.get("status")
                if type(status) is not int:
                    raise FeishuResponseError("飞书移动任务缺少有效的 status")
                if status == 0:
                    node = _object(result.get("node"), "node")
                    node_token = node.get("node_token")
                    if not isinstance(node_token, str) or not node_token:
                        raise FeishuResponseError("飞书移动结果缺少 node_token")
                    return node_token
                # 任务结果只在结束时返回；非 0 状态是最终失败，不再轮询。
                raise FeishuImportError(status, str(result.get("status_msg") or ""))
            if asyncio.get_running_loop().time() >= deadline:
                raise FeishuImportError(-1, "移动任务超时")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def _object(value: JsonValue, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise FeishuResponseError(f"飞书响应缺少有效的 {name} 对象")
    return value


def _image_mime(file_name: str) -> str:
    suffix = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        suffix, "application/octet-stream"
    )


def _json_response(response: httpx.Response) -> dict[str, JsonValue]:
    try:
        data = response.json()
    except ValueError:
        raise FeishuResponseError("飞书响应不是有效 JSON") from None
    if not isinstance(data, dict):
        raise FeishuResponseError("飞书响应缺少 JSON 对象")
    return data


def _raise_on_error(data: dict[str, JsonValue], response: httpx.Response) -> None:
    code = data.get("code")
    if type(code) is not int:
        raise FeishuResponseError("飞书响应缺少有效的 code")
    if code != 0:
        error = data.get("error")
        log_id = error.get("log_id") if isinstance(error, dict) else None
        raise FeishuAPIError(code, response.status_code, log_id if isinstance(log_id, str) else None)
