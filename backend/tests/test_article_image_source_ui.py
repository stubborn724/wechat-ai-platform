"""文章创建页图片来源分流的静态契约测试。

当前前端尚未引入组件测试框架，因此这里通过检查 Vue 单文件组件的关键绑定，
锁定封面与正文必须使用不同状态和请求字段。测试关注业务契约而非具体样式，
避免后续调整布局时误把封面图片重新传入正文图片列表。
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_test_tables():
    """本文件只读取前端源码，不需要连接或清理本地数据库。"""
    yield


@pytest.fixture()
def article_create_source() -> str:
    """读取文章创建页源码，统一处理 Windows 与 Linux 的仓库路径。"""
    project_root = Path(__file__).resolve().parents[2]
    return (project_root / "frontend/src/views/article/ArticleCreateView.vue").read_text(
        encoding="utf-8"
    )


def test_body_image_sources_explicitly_distinguish_local_and_erp(article_create_source: str):
    """正文必须明确展示本地与 ERP 两个入口，不能继续合并成模糊的“素材库”。"""
    assert "正文配图来源（可多选）" in article_create_source
    assert "正文使用本地素材库" in article_create_source
    assert "正文使用 ERP 产品库" in article_create_source


def test_cover_and_body_images_use_independent_request_fields(article_create_source: str):
    """封面与正文必须分别提交，防止任一选图操作污染另一条数据流。"""
    assert "payload.selected_cover_image_url = selectedCoverImageUrl.value" in article_create_source
    assert "payload.selected_image_urls = selectedBodyImageUrls.value" in article_create_source


def test_body_image_picker_is_not_shown_for_pure_image_jobs(article_create_source: str):
    """纯图片任务没有正文，不能复用“正文配图”控件造成两个正文入口。"""
    assert 'v-if="contentType === \'article\'" label="正文配图来源（可多选）"' in article_create_source


def test_cover_validation_only_applies_to_article_jobs(article_create_source: str):
    """隐藏的封面字段不能阻止纯图片或视频任务创建。"""
    expected_scope = (
        "if (contentType.value === 'article') {\n"
        "    // 图文文章的封面与正文来源"
    )
    assert expected_scope in article_create_source
