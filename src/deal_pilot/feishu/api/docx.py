"""新版文档（Docx）读取接口规格，所有方法均未实现。"""

from deal_pilot.feishu.api.models import DocxBlock, DocxDocument


class DocxAPI:
    """读取新版文档元信息与全部结构化块；不支持旧版 doc。"""

    async def get_document(self, document_id: str) -> DocxDocument | None:
        """读取文档元信息，不读取正文。

        Args:
            document_id: 新版文档 ID；知识库节点须先确认 obj_type 为 docx，
                再使用 obj_token，不能直接传 node_token。

        Returns:
            文档 ID、标题和版本号；明确不存在时返回 None。
            无权限、认证失败或服务错误应抛出异常。
        """
        raise NotImplementedError("DocxAPI.get_document 尚未实现")

    async def list_blocks(
        self,
        document_id: str,
        *,
        document_revision_id: int = -1,
    ) -> list[DocxBlock]:
        """读取指定文档的全部块，内部完成翻页，保留富文本结构但不组装树。

        Args:
            document_id: 新版文档 ID。
            document_revision_id: -1 表示最新版本；正整数指定版本。
                需要固定版本时，可先读取文档信息，再传入其 revision_id。

        Returns:
            全部文档块的扁平列表，保持服务返回顺序；每块 raw 保存完整原始 JSON。
            无块时返回空列表；可根据 parent_id、children 在上层组装树。
            任一页读取失败、文档不存在或无权限时应抛出异常，不返回部分列表。
        """
        raise NotImplementedError("DocxAPI.list_blocks 尚未实现")
