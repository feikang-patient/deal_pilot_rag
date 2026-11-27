# 飞书存储副本的局部契约

- 父关系字段使用 `parent_block_id`，与 loader、splitter 和正式 Schema 同名；指向最近的上级可结构化节点的来源 `block_id`，根为 None。
- `schema.py:create_schema() -> CollectionSchema` 无参数，`dense` 固定 1024 维；正式源码是 `create_schema(dim: int)`。本副本仍用旧字段名 `node_id`、`obj_id`，并且缺少正式 Schema 已声明的 `space_id`、`url`、`parent_node_token`；`parent_block_id` 与正式源码一致。本地 Schema 含手动 VARCHAR 主键、来源元数据、切片位置、2048 字节正文、中文分析器和 BM25 稀疏向量，开启动态字段；它只构造 Schema，不创建集合。
- `index_params.py` 的稠密参数工厂读取 `RagStoreConfig.index_type/metric_type`，不读取构建参数；稀疏索引固定 `SPARSE_INVERTED_INDEX/BM25`。`has_index_changed` 只比较类型与度量，`drop_index` 先释放集合再删除稠密索引；辅助函数复用调用方客户端，不负责关闭。异常及取消原样传播，不能保证所有索引缺失情况都能自动重建。
- 本目录 `init_store.py` 的 Schema 和索引辅助函数导入自 `deal_pilot.rag.store.feishu_doc`，没有使用同目录副本；它仍以无参数 `create_schema()` 调用正式工厂。按当前正式源码签名，集合缺失分支会因缺少 `dim` 参数而失败，不能称为独立可用的初始化实现。
- 初始化从 Nacos `rag_config.store["feishu_doc"]` 读取索引配置，连接地址/目标数据库来自 `milvus_settings`；先创建缺失数据库并切换，再处理固定集合 `feishu_doc`。已有集合只比较正式源码的稠密索引，变化时释放、删除、重建后重新加载；不迁移 Schema 或检查稀疏索引，未变化不重新加载。
- 初始化统一在 `finally` 中关闭客户端，已完成的数据库操作不回滚；即使后续因接口差异失败，也可能已创建数据库。该差异是当前实现限制，维护指令不代表已修复源码。
