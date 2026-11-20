# 飞书接入

## 职责与当前状态

- `api` 定义知识库与新版文档（Docx）的只读接口及返回模型；MVP 不包含电子表格。
- 当前只有待审阅的规格骨架：API 类未定义自有构造函数，读取方法均抛出 `NotImplementedError`。
- 没有客户端、鉴权、Token 缓存、网络请求或业务接入；不能据此声称已能读取飞书数据。

## 约定

- 两个 API 类使用 Python 默认构造；未来实现读取时直接使用已有 `config/feishu_settings.py:feishu_settings`，本轮不修改配置。
- 知识库的 `node_token` 与实际内容的 `obj_token` 不可混用；先判断 `obj_type` 再交给内容 API。
- 本目录不负责结构化解析、切分、索引、检索或 Agent 工具注册。
- 用户当前仅授权搭建规格，须在用户确认后另行实现功能。
