"""验证 upload_bytes 的请求参数；不连接真实 MinIO。"""

import asyncio
from typing import Any

import pytest

from deal_pilot.utils.minio import CacheStrategy, ContentTypeStrategy, MinIO


class FakeClient:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def put_object(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def make_client(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, Any]],
    **kwargs: Any,
) -> MinIO:
    client = MinIO(
        endpoint="localhost:9000",
        access_key="access",
        secret_key="secret",
        secure=False,
        **kwargs,
    )
    monkeypatch.setattr(client, "_client", lambda: FakeClient(calls))
    return client


def test_upload_bytes_uses_given_mime_and_default_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    client = make_client(monkeypatch, calls)
    asyncio.run(
        client.upload_bytes(
            bucket_name="feishu-images", filename="img.png", data=b"\x89PNG", mime="image/png"
        )
    )
    assert calls == [{
        "Bucket": "feishu-images",
        "Key": "img.png",
        "Body": b"\x89PNG",
        "CacheControl": "no-store",
        "ContentType": "image/png",
    }]


def test_upload_bytes_applies_cache_strategy_but_not_content_type_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    client = make_client(
        monkeypatch,
        calls,
        cache_strategy=[CacheStrategy(extensions=[".png"], max_age=60)],
        content_type_strategy=ContentTypeStrategy(
            default_content_type="application/octet-stream",
            extensions={".png": "image/x-wrong"},
        ),
    )
    asyncio.run(
        client.upload_bytes(
            bucket_name="feishu-images", filename="/img.png", data=b"x", mime="image/webp"
        )
    )
    assert calls[0]["Key"] == "img.png"
    assert calls[0]["CacheControl"] == "max-age=60"
    assert calls[0]["ContentType"] == "image/webp"
    assert calls[0]["Body"] == b"x"


def test_upload_bytes_allows_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    client = make_client(monkeypatch, calls)
    asyncio.run(
        client.upload_bytes(bucket_name="bucket", filename="a", data=b"", mime="image/png")
    )
    assert calls[0]["Body"] == b""


@pytest.mark.parametrize(
    ("bucket_name", "filename", "data", "mime"),
    [
        ("bucket", "a", bytearray(b"x"), "image/png"),
        ("bucket", "a", "text", "image/png"),
        ("bucket", "a", b"x", ""),
        ("bucket", "a", b"x", "image/png\r\nX: y"),
        ("B", "a", b"x", "image/png"),
        ("bucket", "", b"x", "image/png"),
    ],
)
def test_upload_bytes_rejects_invalid_arguments(
    monkeypatch: pytest.MonkeyPatch,
    bucket_name: str,
    filename: str,
    data: Any,
    mime: str,
) -> None:
    calls: list[dict[str, Any]] = []
    client = make_client(monkeypatch, calls)
    with pytest.raises(ValueError):
        asyncio.run(
            client.upload_bytes(
                bucket_name=bucket_name, filename=filename, data=data, mime=mime
            )
        )
    assert calls == []
