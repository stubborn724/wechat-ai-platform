"""ERP 产品与室内场景匹配策略的单元测试。

这些测试验证场景约束在进入大模型前已经被程序确定，不依赖外部 ERP、图片模型或
知识库服务。这样可以确保“餐桌不能带客厅沙发”这类硬边界不会被一次模型输出
悄悄绕过，同时也不会为了场景判断增加额外 token 消耗。
"""

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本模块只测试纯函数规则，不需要触发全局数据库清理夹具。"""

    yield


def test_dining_table_uses_dining_room_and_excludes_living_room_furniture():
    """餐桌类产品必须进入餐厅，并明确排除沙发等客厅主体。"""

    from app.services.scheduled_product_scene_service import (
        resolve_product_scene_profile,
    )

    profile = resolve_product_scene_profile(
        "CZ20264303454 椭圆餐桌",
        tags=["现代极简", "餐桌", "常规产品"],
    )

    assert profile.key == "dining_table"
    assert "餐厅" in profile.required_rooms
    assert "餐椅" in profile.allowed_elements
    assert "沙发" in profile.forbidden_elements
    assert "茶几" in profile.forbidden_elements


def test_specific_dining_sideboard_rule_wins_over_generic_cabinet_rule():
    """餐边柜不能被通用柜体规则误判成卧室或客厅柜体。"""

    from app.services.scheduled_product_scene_service import (
        resolve_product_scene_profile,
    )

    profile = resolve_product_scene_profile("现代餐边柜", tags=["柜类", "餐厅家具"])

    assert profile.key == "dining_sideboard"
    assert "餐厅" in profile.required_rooms
    assert "餐桌" in profile.allowed_elements


def test_sanitize_scene_description_removes_conflicting_terms():
    """模型返回“餐桌与沙发”时，槽位描述不能把冲突物继续传给生图模型。"""

    from app.services.scheduled_product_scene_service import (
        resolve_product_scene_profile,
        sanitize_product_scene_text,
    )

    profile = resolve_product_scene_profile("椭圆餐桌")
    result = sanitize_product_scene_text(
        "餐桌与沙发，客厅沙发旁的暖光家居摆设，茶几作为搭配",
        profile,
        fallback_subject="椭圆餐桌",
    )

    assert "沙发" not in result
    assert "茶几" not in result
    assert "客厅" not in result
    assert "餐厅" in result
    assert "椭圆餐桌" in result


def test_bed_uses_bedroom_and_removes_sofa_related_elements():
    """床品图必须锁定卧室，槽位文本中的沙发或客厅陈设不得传入生图提示。"""

    from app.services.scheduled_product_scene_service import (
        resolve_product_scene_profile,
        sanitize_product_scene_text,
    )

    profile = resolve_product_scene_profile("FSC2023042902 软包床")
    result = sanitize_product_scene_text(
        "软包床与沙发并置，客厅沙发旁放置贵妃榻和休闲榻",
        profile,
        fallback_subject="FSC2023042902 软包床",
    )

    assert profile.key == "bed"
    assert "卧室" in profile.required_rooms
    assert "沙发" in profile.forbidden_elements
    assert "沙发" not in result
    assert "贵妃榻" not in result
    assert "休闲榻" not in result
    assert "客厅" not in result
    assert "卧室" in result


def test_bed_title_uses_product_scene_and_never_falls_back_to_living_room():
    """床类标题必须使用卧室语义，不能把 ERP 型号直接作为公众号标题。"""

    from app.services.scheduled_product_scene_service import resolve_product_scene_profile
    from app.services.scheduled_product_title_service import normalize_scheduled_product_title

    profile = resolve_product_scene_profile("C2025111479 家具单品", tags=["床类"])
    title = normalize_scheduled_product_title(
        "C2025111479 家具单品",
        profile=profile,
        candidate_title="C2025111479 家具单品",
    )

    assert title.startswith("床|")
    assert "客厅" not in title
    assert "C2025111479" not in title


def test_bed_html_slot_text_removes_living_room_conflict():
    """床类 HTML 文案槽位不得保留客厅或沙发等冲突场景词。"""

    from app.services.scheduled_product_scene_service import (
        resolve_product_scene_profile,
        sanitize_article_scene_text,
    )

    profile = resolve_product_scene_profile("C2025111479 家具单品", tags=["床类"])
    result = sanitize_article_scene_text(
        "光线穿过客厅，床在沙发旁重新塑造客厅格局。",
        profile,
    )

    assert "客厅" not in result
    assert "沙发" not in result


def test_product_scene_guard_is_idempotent_and_contains_negative_constraints():
    """最终图生图提示词要稳定追加场景正向与反向约束，重复编排不增长。"""

    from app.services.scheduled_product_scene_service import (
        append_product_scene_guard,
        resolve_product_scene_profile,
    )

    profile = resolve_product_scene_profile("椭圆餐桌")
    prompt = append_product_scene_guard(
        "主体：椭圆餐桌，暖色现代家居",
        profile,
        product_name="椭圆餐桌",
    )
    repeated = append_product_scene_guard(prompt, profile, product_name="椭圆餐桌")

    assert "【产品-场景一致性硬约束】" in prompt
    assert "必须场景：餐厅" in prompt
    assert "禁止出现：" in prompt
    assert "沙发" in prompt
    assert repeated == prompt


def test_product_scene_guard_explicitly_blocks_wrong_room_furniture_for_bed():
    """床类产品的海报场景必须明确排除客厅和餐厅家具，避免画面跑偏。"""

    from app.services.scheduled_product_scene_service import (
        append_product_scene_guard,
        resolve_product_scene_profile,
    )

    profile = resolve_product_scene_profile("单人床")
    prompt = append_product_scene_guard(
        "主体：单人床，静谧卧室",
        profile,
        product_name="单人床",
    )

    assert profile.key == "bed"
    assert "卧室" in prompt
    assert "沙发" in prompt
    assert "餐桌" in prompt
    assert "客厅" in prompt
    assert "床品专属约束" in prompt


def test_product_scene_guard_explicitly_blocks_wrong_room_furniture_for_sofa_and_table():
    """沙发与餐桌也必须锁定自己的功能空间，不能混入卧室家具。"""

    from app.services.scheduled_product_scene_service import (
        append_product_scene_guard,
        resolve_product_scene_profile,
    )

    sofa_profile = resolve_product_scene_profile("单人沙发椅")
    sofa_prompt = append_product_scene_guard(
        "主体：单人沙发椅，安静客厅",
        sofa_profile,
        product_name="单人沙发椅",
    )
    table_profile = resolve_product_scene_profile("圆餐桌")
    table_prompt = append_product_scene_guard(
        "主体：圆餐桌，温暖用餐空间",
        table_profile,
        product_name="圆餐桌",
    )

    assert sofa_profile.key == "sofa"
    assert "沙发专属约束" in sofa_prompt
    assert "餐桌" in sofa_prompt
    assert "床具" in sofa_prompt
    assert table_profile.key == "dining_table"
    assert "餐桌专属约束" in table_prompt
    assert "床" in table_prompt
    assert "卧室" in table_prompt


def test_html_slot_compiler_removes_conflicting_alt_text_for_erp_product():
    """HTML 槽位编译后的 alt 和生图补充词不能继续携带客厅沙发。"""

    from app.services.article_agent_service import _compose_html_image_slot_prompts
    from app.services.scheduled_product_scene_service import (
        resolve_product_scene_profile,
    )

    profile = resolve_product_scene_profile("椭圆餐桌")
    result = _compose_html_image_slot_prompts(
        {
            "image-1": {
                "keywords": "餐桌与沙发，客厅摆设",
                "prompt": "餐桌放在沙发旁，暖色家居空间",
            }
        },
        {},
        set(),
        product_scene_profile=profile.to_payload(),
        product_name="椭圆餐桌",
    )

    assert "沙发" not in result["image-1"]["keywords"]
    assert "客厅" not in result["image-1"]["prompt"]
    assert "餐厅" in result["image-1"]["keywords"]
