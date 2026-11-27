# 飞书接入

## 职责与当前状态

- `api` 已实现应用身份的知识库与新版文档（Docx）只读接口、文档图片下载，以及把本地 Markdown 导入为 Docx 并移动进知识空间的发布接口；不包含电子表格或旧版 doc。
- 使用飞书官方 `lark-oapi` SDK，当前锁文件版本为 1.7.4；HTTP、Token 获取与缓存均由 SDK 管理，没有手写 HTTP 客户端或 Token 缓存。
- 发布能力目前只由一次性脚本 `scripts/import_feishu_kb.py` 调用；尚未接入业务入口、Agent 工具、结构化解析、切分、索引或检索。

## 约定

- 使用 `from deal_pilot.feishu.api import WikiAPI, DocxAPI, ImageAPI, PublishAPI`，四个 API 类使用 Python 默认构造，没有模块级 API 实例。
- 直接使用 `config/feishu_settings.py:feishu_settings` 中的 app_id/app_secret；导入不请求网络，缺少配置在调用时抛出 FeishuConfigurationError。
- 知识库的 `node_token` 与实际内容的 `obj_token` 不可混用；先判断 `obj_type` 再交给内容 API。
- 本目录不负责结构化解析、切分、索引、检索或 Agent 工具注册。
- 读取与发布方法均为 async；`list_spaces`、`list_nodes` 用 await 取列表，`list_all_space_nodes` 返回异步迭代器、调用方用 `async for` 消费；请求、缓存与错误细节见 `api/AGENTS.md`。
- 发布需要应用被加为目标知识空间的管理员/成员；创建知识空间接口不支持应用身份，空间由用户手动创建。
- 当前 SDK 的异步接口仍包含同步鉴权，因此完整同步 SDK 调用放入 asyncio.to_thread，不直接在事件循环中调用同步 SDK。

## 验证边界

- `tests/units/test_feishu.py` 执行真实 SDK 的请求构造、鉴权、Token 缓存和响应解析，只 Mock 底层 requests.request；覆盖分页、遍历、原始块、图片下载及异常，不需要真实凭据或外部服务。
- `tests/units/test_feishu_publish.py` 用假 Drive/Wiki 客户端验证导入与移动的请求构造、轮询和失败语义，不发网络请求。
- 读取已用示例空间做过真实调用；18 篇 Markdown 已真实导入 6 个知识空间，9 张图片插入、16 个表格保留，
  导入/补图可重复执行；6 个首页与 2 篇重复文档已通过 `scripts/feishu_cleanup.py` 真实删除。
