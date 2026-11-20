"""知识库读取接口规格，所有方法均未实现。"""

from deal_pilot.feishu.api.models import WikiNode, WikiSpace


class WikiAPI:
    """读取应用身份有权限访问的知识空间及节点；不包含文档正文读取。"""

    async def list_spaces(self) -> list[WikiSpace]:
        """读取当前应用身份可访问的全部知识空间，内部完成翻页。

        Returns:
            全部可访问空间的列表；没有空间时返回空列表。
            任一页读取失败应抛出异常，不将部分列表作为完整结果返回。
        """
        raise NotImplementedError("WikiAPI.list_spaces 尚未实现")

    async def get_space(self, space_id: str) -> WikiSpace | None:
        """读取指定知识空间基本信息。

        Args:
            space_id: 知识空间 ID，不接受知识库链接或 node_token。

        Returns:
            空间 ID、名称和描述；明确不存在时返回 None。
            无权限、认证失败或服务错误应抛出异常。
        """
        raise NotImplementedError("WikiAPI.get_space 尚未实现")

    async def get_node(self, node_token: str) -> WikiNode | None:
        """读取一个知识库节点，解析其实际内容类型与 token。

        Args:
            node_token: 知识库链接 /wiki/ 后面的 token，不是实际内容 obj_token。

        Returns:
            节点及 obj_type、obj_token、url；url 按
            https://www.feishu.cn/wiki/{node_token} 拼接，正文需继续调用 DocxAPI。
            明确不存在时返回 None；无权限、认证失败或服务错误应抛出异常。
        """
        raise NotImplementedError("WikiAPI.get_node 尚未实现")

    async def list_nodes(
        self,
        space_id: str,
        *,
        parent_node_token: str | None = None,
    ) -> list[WikiNode]:
        """读取全部直接子节点，内部完成翻页；不递归遍历后代。

        Args:
            space_id: 知识空间 ID。
            parent_node_token: 父节点 token；None 表示读取空间根目录的一级节点。

        Returns:
            指定父节点下的全部直接子节点；空目录返回空列表。
            任一页读取失败应抛出异常，不返回部分列表。
        """
        raise NotImplementedError("WikiAPI.list_nodes 尚未实现")

    async def list_all_space_nodes(self) -> list[WikiNode]:
        """读取所有可访问知识空间中、全部层级的节点，内部完成翻页和递归遍历。

        Returns:
            所有可访问空间的节点扁平列表，包含根目录节点及全部后代节点。
            每项通过 space_id 标识所属空间；同一 (space_id, node_token) 只返回一次，
            指向相同 obj_token 的不同节点仍分别保留。没有节点时返回空列表。
            任一空间或节点读取失败应抛出异常，不静默跳过或返回部分列表。
            不读取节点关联的文档正文。
        """
        raise NotImplementedError("WikiAPI.list_all_space_nodes 尚未实现")
