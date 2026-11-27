"""PublishAPI 的请求构造、任务轮询与失败语义；不访问真实飞书。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from deal_pilot.feishu.api import publish
from deal_pilot.feishu.api.errors import FeishuImportError, FeishuResponseError
from deal_pilot.feishu.api.publish import PublishAPI


class FakeRaw:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.headers = {"x-tt-logid": "log-1"}
        self.content = json.dumps(payload).encode()


class FakeResponse:
    def __init__(self, data, *, code=0, status=200):
        self.code = code
        self.raw = FakeRaw({"code": code, "msg": "success", "data": data}, status)

    def success(self):
        return self.code == 0


class FakeMedia:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def upload_all(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


class FakeImportTask:
    def __init__(self, responses):
        self.responses = responses
        self.create_requests = []
        self.get_requests = []

    def create(self, request):
        self.create_requests.append(request)
        return self.responses.pop(0)

    def get(self, request):
        self.get_requests.append(request)
        return self.responses.pop(0)


class FakeDrive:
    def __init__(self, media, create, get):
        self.media = FakeMedia(media)
        self.import_task = FakeImportTask(create + get)


class FakeSpaceNode:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def move_docs_to_wiki(self, request):
        self.requests.append(request)
        return self.response


class FakeTask:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def get(self, request):
        self.requests.append(request)
        return self.response


def fake_wiki(space_node, task=None):
    return SimpleNamespace(space_node=space_node, task=task)


def run(coroutine):
    return asyncio.run(coroutine)


def test_import_markdown_builds_import_request(monkeypatch):
    drive = FakeDrive(
        [FakeResponse({"file_token": "file-1"})],
        [FakeResponse({"ticket": "ticket-1"})],
        [FakeResponse({"result": {"job_status": 0, "token": "docx-1"}})],
    )
    monkeypatch.setattr(publish, "drive_client", lambda: drive)
    monkeypatch.setattr(publish, "_POLL_INTERVAL_SECONDS", 0)

    token = run(
        PublishAPI().import_markdown(file_name="PROD-001.md", content=b"# hi", title="PROD-001")
    )

    assert token == "docx-1"
    upload = drive.media.requests[0]
    # 同步传输层只处理 body，multipart 由 SDK 资源方法转换；不能再依赖 request.files。
    assert upload.files is None
    assert upload.body.file[0] == "PROD-001.md"
    assert upload.body.file[2] == "text/markdown"
    assert upload.body.file[1].read() == b"# hi"
    assert json.loads(upload.body.extra) == {"obj_type": "docx", "file_extension": "md"}
    assert upload.body.parent_type == "ccm_import_open"
    create = drive.import_task.create_requests[0]
    assert create.body.type == "docx"
    assert create.body.file_extension == "md"
    assert create.body.file_name == "PROD-001"
    assert create.body.point.mount_type == 1


@pytest.mark.parametrize("status", [110, 112, 115])
def test_import_failure_status_raises(monkeypatch, status):
    drive = FakeDrive(
        [FakeResponse({"file_token": "file-1"})],
        [FakeResponse({"ticket": "ticket-1"})],
        [FakeResponse({"result": {"job_status": status, "job_error_msg": "失败"}})],
    )
    monkeypatch.setattr(publish, "drive_client", lambda: drive)

    with pytest.raises(FeishuImportError) as error:
        run(PublishAPI().import_markdown(file_name="a.md", content=b"x", title="a"))
    assert error.value.status == status


def test_import_pending_then_retries(monkeypatch):
    drive = FakeDrive(
        [FakeResponse({"file_token": "file-1"})],
        [FakeResponse({"ticket": "ticket-1"})],
        [
            FakeResponse({"result": {"job_status": 2}}),
            FakeResponse({"result": {"job_status": 0, "token": "docx-1"}}),
        ],
    )
    monkeypatch.setattr(publish, "drive_client", lambda: drive)
    monkeypatch.setattr(publish, "_POLL_INTERVAL_SECONDS", 0)

    token = run(PublishAPI().import_markdown(file_name="a.md", content=b"x", title="a"))
    assert token == "docx-1"
    assert len(drive.import_task.get_requests) == 2


def test_attach_returns_wiki_token_directly(monkeypatch):
    space_node = FakeSpaceNode(FakeResponse({"wiki_token": "wik-1"}))
    monkeypatch.setattr(publish, "wiki_client", lambda: fake_wiki(space_node))

    token = run(PublishAPI().attach_docx_to_wiki(space_id="s1", docx_token="d1"))

    assert token == "wik-1"
    request = space_node.requests[0]
    assert request.space_id == "s1"
    assert request.body.obj_type == "docx"
    assert request.body.obj_token == "d1"


def test_attach_polls_async_move_task(monkeypatch):
    space_node = FakeSpaceNode(FakeResponse({"task_id": "task-1"}))
    task = FakeTask(FakeResponse({"task": {"move_result": [
        {"status": 0, "node": {"node_token": "wik-2"}}
    ]}}))
    monkeypatch.setattr(publish, "wiki_client", lambda: fake_wiki(space_node, task))
    monkeypatch.setattr(publish, "_POLL_INTERVAL_SECONDS", 0)

    token = run(PublishAPI().attach_docx_to_wiki(space_id="s1", docx_token="d1"))

    assert token == "wik-2"
    assert ("task_type", "move") in task.requests[0].queries


def test_attach_move_failure_raises(monkeypatch):
    space_node = FakeSpaceNode(FakeResponse({"task_id": "task-1"}))
    task = FakeTask(FakeResponse({"task": {"move_result": [
        {"status": 1, "status_msg": "无权限"}
    ]}}))
    monkeypatch.setattr(publish, "wiki_client", lambda: fake_wiki(space_node, task))
    monkeypatch.setattr(publish, "_POLL_INTERVAL_SECONDS", 0)

    with pytest.raises(FeishuImportError):
        run(PublishAPI().attach_docx_to_wiki(space_id="s1", docx_token="d1"))


def test_invalid_input_fails_before_network(monkeypatch):
    def boom():
        raise AssertionError("不应创建客户端")

    monkeypatch.setattr(publish, "drive_client", boom)

    with pytest.raises(ValueError):
        run(PublishAPI().import_markdown(file_name="a.txt", content=b"x", title="a"))
    with pytest.raises(ValueError):
        run(PublishAPI().import_markdown(file_name="a.md", content=b"x", title=" "))


def test_missing_result_object_raises(monkeypatch):
    drive = FakeDrive(
        [FakeResponse({"file_token": "file-1"})],
        [FakeResponse({"ticket": "ticket-1"})],
        [FakeResponse({"wrong": {}})],
    )
    monkeypatch.setattr(publish, "drive_client", lambda: drive)

    with pytest.raises(FeishuResponseError):
        run(PublishAPI().import_markdown(file_name="a.md", content=b"x", title="a"))
