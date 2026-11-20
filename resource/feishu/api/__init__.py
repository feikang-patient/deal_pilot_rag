"""飞书只读 API 规格；仅导出类，不创建客户端或模块级 API 实例。"""

from deal_pilot.feishu.api.docx import DocxAPI
from deal_pilot.feishu.api.wiki import WikiAPI

__all__ = ["WikiAPI", "DocxAPI"]
