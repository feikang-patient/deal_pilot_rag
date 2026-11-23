"""应用身份的云文档图片素材下载。"""

from lark_oapi.api.drive.v1 import DownloadMediaRequest

from deal_pilot.feishu.api._client import media_client, resource_id, sdk_download
from deal_pilot.feishu.api.errors import FeishuResponseError
from deal_pilot.feishu.api.models import ImageData


class MediaAPI:
    """按素材 token 下载云文档中的图片；不包含写入、删除或非图片素材。"""

    async def download_image(self, image_token: str) -> ImageData:
        """下载图片块（Image Block）对应的图片素材。

        Args:
            image_token: 图片块的 image.token；可通过 DocxAPI.list_blocks 读取，
                不能传知识库 node_token 或文档 ID。

        Returns:
            mime 来自响应头的 Content-Type，去掉参数并转为小写；
            data 为图片的完整原始字节。

        参数错误抛出 ValueError；缺少配置、认证失败、业务错误、
        非正常 HTTP 状态或协议结构错误时抛出对应异常，不返回部分内容。
        服务返回的 Content-Type 不是 image/* 时抛出 FeishuResponseError，
        不将附件或其他素材当作图片。
        """
        image_token = resource_id(image_token, "image_token")
        api = media_client()
        request = DownloadMediaRequest.builder().file_token(image_token).build()
        mime, data = await sdk_download(lambda: api.media.download(request))
        if not mime.startswith("image/"):
            raise FeishuResponseError("飞书素材不是图片")
        return ImageData(mime=mime, data=data)
