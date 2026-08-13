"""ERP 图生图的多机位与联系方式隔离纯单元测试。"""


def test_erp_image_viewpoint_instruction_rotates_five_slots_and_forbids_contact_text():
    """同一篇 ERP 文章的 5 张配图必须分配不同机位，联系方式不能交给生图模型。"""

    from app.services.scheduled_product_scene_service import (
        append_erp_image_viewpoint_instruction,
    )

    instructions = [
        append_erp_image_viewpoint_instruction("主体：现代床", position, total=5)
        for position in range(1, 6)
    ]

    assert len(set(instructions)) == 5
    assert "正面三分之四" in instructions[0]
    assert "反向三分之四" in instructions[1]
    assert "侧面" in instructions[2]
    assert "材质细节" in instructions[3]
    assert "生活空间广角" in instructions[4]
    assert all("电话" in instruction for instruction in instructions)
    assert all("二维码" in instruction for instruction in instructions)
    assert all("可读文字" in instruction for instruction in instructions)
