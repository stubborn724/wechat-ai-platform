"""文章详情页前端状态更新的回归测试。

该测试不启动浏览器，也不依赖业务数据库，仅检查组件中的关键状态更新契约。
这样既能覆盖曾经导致页面白屏的 ``const ref`` 重赋值问题，也不会触碰本地真实数据。
"""

from pathlib import Path


ARTICLE_DETAIL_VIEW = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "views"
    / "article"
    / "ArticleDetailView.vue"
)


def test_metrics_timestamp_updates_ref_value_instead_of_reassigning_const() -> None:
    """指标更新时间必须写入 ref.value，避免运行时给 const 变量重新赋值。"""

    source = ARTICLE_DETAIL_VIEW.read_text(encoding="utf-8")

    # Vue 的 ref 返回值本身使用 const 保存，业务数据只能更新其 value 属性。
    assert "metricsUpdatedAt.value = m.updated_at || ''" in source
    assert "\n    metricsUpdatedAt = m.updated_at || ''" not in source
