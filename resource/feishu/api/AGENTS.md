# 飞书只读 API 规格

## 文件职责

- `wiki.py:WikiAPI`：全量知识空间列表、空间信息、节点信息、全量直接子节点列表及全部空间的全层级节点列表。
- `docx.py:DocxAPI`：仅包含新版文档元信息查询和全量块列表；不含旧版 doc。
- `models.py`：应用层 Pydantic 返回模型；不是飞书官方响应结构的完整复刻。
- `__init__.py`：仅导出 `WikiAPI` 和 `DocxAPI`，没有模块级实例或隐式初始化。

## 待审阅设计

- 目标：先确定读取接口的参数及返回值，再实现飞书调用。
- 选定方案：MVP 仅保留知识库与 Docx；网络读取方法为 async；两个类不定义自有构造函数，
  约定直接使用 `config/feishu_settings.py` 中的 `feishu_settings`；暂按应用身份设计。
- 原因：先完成知识库目录与文档读取，电子表格不纳入 MVP，控制接口范围。
- 实现状态：全部读取方法仅抛出 `NotImplementedError`，类使用 Python 默认构造，没有调用链接入。

## 拟定契约（尚未实现）

- `WikiNode.obj_type` 使用 `Literal["docx"]`，MVP 仅支持新版文档；其他节点类型的处理在实现阶段明确。
- `WikiNode.url` 为必填、非空 `str`，统一按 `https://www.feishu.cn/wiki/{node_token}` 拼接，
  使用节点 token，不使用 `obj_token`，不依赖接口是否返回 URL。该规则参考
  [官方 CLI 的 URL 构造方式](https://github.com/larksuite/cli/blob/main/shortcuts/common/resource_url.go)；
  当前仅定义字段和拼接契约，尚未实现数据获取或自动填充。
- `list_spaces()` 返回全部可访问空间的 `list[WikiSpace]`；不暴露分页参数，内部完成翻页。
- `list_nodes(space_id, *, parent_node_token=None)` 返回全部直接子节点的 `list[WikiNode]`；内部完成翻页，不递归。
- `list_all_space_nodes()` 返回全部可访问空间、全部层级节点的扁平 `list[WikiNode]`；内部翻页和递归，
  按 `(space_id, node_token)` 去重，不按 `obj_token` 合并不同节点，不读取文档正文。
- 全量列表读取遇到任一页、空间或节点失败应抛出异常，不静默跳过或将部分结果当作全量返回。
- `DocxAPI` 仅保留 `get_document(document_id)` 和 `list_blocks(document_id, *, document_revision_id=-1)`。
- `list_blocks` 返回全量 `list[DocxBlock]`，内部完成翻页，不暴露分页参数，不组装树；
  保留可选的文档版本参数，文档不存在或读取失败应抛出异常。
- 按 ID 查询的 `get_*` 方法明确不存在时返回 None，返回类型为原类型与 None 的联合；
  无权限、认证失败、限流或网络错误仍应抛出异常。
- 未来实现需区分参数错误与认证、服务错误；错误映射、HTTP/SDK 选型、Token 生命周期尚未实现。
- `DocxBlock.raw` 保留完整原始块 JSON，避免未知类型或富文本字段丢失。
- 未纳入导出、写入、删除、图片/附件下载、Markdown 转换、自动重试或用户级权限过滤。

## 官方文档与验证边界

- [知识空间列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space/list)
- [知识空间信息](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space/get)
- [知识节点信息](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node)
- [直接子节点](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/list)
- [新版文档指南](https://open.larkoffice.com/document/server-docs/docs/docs/docx-v1/guide)

本轮核对了官方资源入口；部分页面正文无法通过检索工具读取。具体参数上限、授权范围和响应字段
仍需在实现阶段复核。语法、导入和类型检查只验证本地规格，不代表真实飞书租户验证通过。
