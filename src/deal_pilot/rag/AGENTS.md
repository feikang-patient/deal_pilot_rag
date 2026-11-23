# RAG 模块

- 当前 `structure/feishu_doc` 提供树节点、添加/移除节点方法，以及异步 `await build_tree(list[DocxBlock]) -> BlockNode`：校验原始块关系并按标题层级构树，提取纯文本或图片 token 到 content，payload 保存代码语言或表格行列数、合并信息，构树完成后遍历全部图片节点并按文档顺序串行调用图片处理函数，前一张完成后再处理下一张，全部完成再返回。图片处理函数仍为占位；`image_parser` 已提供 Data URL 生成、私有 MinIO 上传和前后文提取辅助函数。尚未实现 Markdown 格式转换、实际图片解析、切分、索引或检索，也未接入 Agent。
- 不将此目录名称或类型定义视为已有完整 RAG 能力；具体结构契约见下级指令。
