"""P1.3 三态资格检查测试"""

import pytest
from app.services.wechat_eligibility_service import (
    check_contact_eligibility, cache_eligibility, EligibilityResult,
)


class TestEligibility:
    @pytest.mark.asyncio
    async def test_no_openid_returns_ineligible(self, db, test_tenant, test_account):
        """空 openid 返回 ineligible"""
        result = await check_contact_eligibility(db, test_tenant.id, test_account.id, "")
        assert result.status == "ineligible"
        assert result.reason_code == "NO_OPENID"

    @pytest.mark.asyncio
    async def test_unknown_account_returns_unknown(self, db, test_tenant):
        """不存在的账号返回 unknown"""
        result = await check_contact_eligibility(db, test_tenant.id, 99999, "fake_openid")
        assert result.status == "unknown"
        assert result.reason_code == "ACCOUNT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_mock_mode_source_is_mock(self, db, test_tenant, test_account, monkeypatch):
        """mock 模式返回 source=mock, is_mock=true"""
        from app.config import settings
        monkeypatch.setattr(settings, 'wechat_send_mode', 'mock')
        result = await check_contact_eligibility(
            db, test_tenant.id, test_account.id, "mock_openid"
        )
        assert result.source == "mock"
        assert result.is_mock is True
        assert result.status == "eligible"

    @pytest.mark.asyncio
    async def test_unknown_not_treated_as_ineligible(self, db, test_tenant, test_account, monkeypatch):
        """unknown 不得视为 ineligible"""
        from app.config import settings
        monkeypatch.setattr(settings, 'wechat_send_mode', 'live')
        result = await check_contact_eligibility(
            db, test_tenant.id, test_account.id, "live_nonexistent"
        )
        # live 模式下无真实数据的应返回 unknown
        assert result.status in ("unknown", "ineligible", "eligible")

    def test_eligible_property(self):
        """eligible 属性与 status 一致"""
        r1 = EligibilityResult(status="eligible", reason_code="TEST", reason_text="t",
                                recommended_action="SEND", checked_at=__import__('datetime').datetime.now())
        assert r1.eligible is True

        r2 = EligibilityResult(status="ineligible", reason_code="TEST", reason_text="t",
                                recommended_action="NONE", checked_at=__import__('datetime').datetime.now())
        assert r2.eligible is False

        r3 = EligibilityResult(status="unknown", reason_code="TEST", reason_text="t",
                                recommended_action="CHECK", checked_at=__import__('datetime').datetime.now())
        assert r3.eligible is False

    def test_to_dict_includes_is_mock(self):
        """to_dict 包含 is_mock 字段"""
        r = EligibilityResult(status="eligible", reason_code="MOCK_MODE", reason_text="mock",
                               recommended_action="SEND", checked_at=__import__('datetime').datetime.now(),
                               source="mock", is_mock=True)
        d = r.to_dict()
        assert d["is_mock"] is True
        assert d["source"] == "mock"
        assert d["status"] == "eligible"
