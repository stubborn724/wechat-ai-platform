"""绣蔓视觉知识库定向刷新配置的纯单元测试。"""


def test_xiuman_refresh_script_targets_only_the_visual_system_document():
    """定向同步只能替换绣蔓背景说明，不能扩散到任务或其他品牌。"""

    from scripts.refresh_xiuman_visual_knowledge import (
        SYSTEM_DOCUMENT_FILENAME,
        XIUMAN_SOURCE_KEY,
    )

    assert XIUMAN_SOURCE_KEY == "xiuman"
    assert SYSTEM_DOCUMENT_FILENAME == "系统生成：背景说明.txt"
