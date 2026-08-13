"""微信公众号平台级进度的纯业务测试。"""

from app.services.tageai_platform_progress_service import project_platform_progress


def test_text_generating_projects_one_platform_progress():
    """正文生成时只给出公众号一条总进度，不泄漏内部内容进度条。"""

    result = project_platform_progress({
        "platform": "wechat",
        "stage": "TEXT_GENERATING",
        "text_progress": 50,
        "media_total": 5,
        "media_ready": 0,
    })

    assert result["platform"] == "wechat"
    assert result["platformLabel"] == "微信公众号"
    assert result["progress"] == 20
    assert result["mediaSummary"] == {
        "total": 5,
        "ready": 0,
        "generating": 0,
        "failed": 0,
    }
    assert "taskBars" not in result


def test_media_progress_uses_ready_slots_and_keeps_publish_unfinished():
    """媒体完成数推进平台总进度，但媒体未齐时不能进入可发布完成态。"""

    result = project_platform_progress({
        "platform": "wechat",
        "stage": "MEDIA_GENERATING",
        "text_progress": 100,
        "media_total": 5,
        "media_ready": 3,
        "media_generating": 1,
        "media_failed": 1,
    })

    assert result["progress"] == 70
    assert result["stage"] == "MEDIA_GENERATING"
    assert result["status"] == "MEDIA_GENERATING"
    assert result["mediaSummary"]["ready"] == 3
    assert result["mediaSummary"]["failed"] == 1
    assert result["estimatedRemainingSeconds"]["min"] > 0


def test_ready_for_publish_is_not_published():
    """冻结版本就绪只代表等待一次用户确认，不能提前显示已发布。"""

    result = project_platform_progress({
        "platform": "wechat",
        "stage": "READY_FOR_PUBLISH",
        "text_progress": 100,
        "media_total": 5,
        "media_ready": 5,
    })

    assert result["status"] == "READY_FOR_PUBLISH"
    assert result["progress"] == 95
    assert result["estimatedRemainingSeconds"] == {"min": 0, "max": 0}


def test_required_media_failure_is_explicit_and_does_not_hide_diagnostics():
    """必需媒体失败时平台任务明确失败，并保留失败数量给用户提示。"""

    result = project_platform_progress({
        "platform": "wechat",
        "stage": "MEDIA_GENERATING",
        "text_progress": 100,
        "media_total": 4,
        "media_ready": 3,
        "media_failed": 1,
        "required_media_failed": 1,
    })

    assert result["status"] == "FAILED"
    assert result["stage"] == "MEDIA_FAILED"
    assert result["mediaSummary"]["failed"] == 1
    assert result["error"]["code"] == "REQUIRED_MEDIA_FAILED"
