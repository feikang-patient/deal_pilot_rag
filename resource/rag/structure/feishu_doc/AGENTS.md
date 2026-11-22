# 飞书文档树

- 目标：将 `DocxAPI.list_blocks()` 返回的 `list[DocxBlock]` 表达为有序树。
- 选定方案：`block_node.py` 中的 `BlockNode` 使用标准库 dataclass，独立保存 `block_id`、`block_type`、`content` 和 JSON 兼容的 `payload`，不持有 `DocxBlock`；通过 `parent`、`children`、`prev`、`next` 引用其他节点。
- 原因：构树后节点应独立包含后续切片所需的信息，无需回查输入列表；所有块共用节点类型，兄弟引用与父节点的有序子列表保持一致。
- 实现状态：已有节点类型及 `append(child)`、`remove()` 关系维护方法；输入 Block 列表构树、内容转换、全树校验、标题归并和序列化均未实现。内容及图片解析方案暂缓设计。
- `append(child)` 将节点及其子树移动到子列表末尾，自动脱离原父节点；同父节点内调用会移到末尾，不重复添加。添加自身或祖先时先抛出 ValueError，不改变原树。
- `remove()` 按对象身份从父节点的子列表中移除当前节点，连接原前后兄弟，清空当前节点的 parent/prev/next；保留内容、payload 和子树内部引用。对根节点或已脱离节点重复调用无副作用；父节点存在但子列表没有当前对象时抛出 ValueError。
- 方法要求输入树关系一致，并维护父子及兄弟引用；直接赋值或修改 children 不会自动校验，不保证修复任意已损坏的树。
- `block_type.py` 的 `BlockType(IntEnum)` 定义已列出的飞书块类型，保留原始整数编号；枚举不代表对应解析器已实现，也不宣称覆盖全部扩展类型。节点的 `block_type: BlockType | int` 允许未列出类型保留原始编号；`UNDEFINED=999` 仅表示飞书自身的未支持块，不能作为所有未知编号的替代值。
- `content` 只存本节点内容，不重复拼接子树；非文本内容及类型专属数据放入 `payload`，后续构建过程负责提取完整信息。
- 初次构树拟恢复原始块关系，子节点顺序以来源 `DocxBlock.children` 为准；调整节点关系不改写输入 Block。
- `prev`、`next` 仅表示同级相邻兄弟；首尾缺失引用为 None，根节点二者均为 None。
- 父子及兄弟双向引用存在循环；后续导出只沿 children 遍历，不能直接调用 dataclasses.asdict。
- 本次可用 `.venv/bin/pyright --pythonpath .venv/bin/python src/deal_pilot/rag/structure/feishu_doc` 检查类型；不依赖真实飞书服务。
- 本模块单元测试由用户在课堂上动态添加，当前不预置测试文件。
