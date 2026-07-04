import pytest
from pydantic import ValidationError

from resolute.config import Settings
from resolute.schemas import AutomationMode


@pytest.mark.parametrize(
    "mode", [AutomationMode.AUTO_PROFILE, AutomationMode.AUTO_APPROVE]
)
def test_auto_write_modes_require_webhook_secret(mode):
    with pytest.raises(ValidationError, match="webhook_shared_secret"):
        Settings(mode=mode, allow_writes=True)


def test_auto_write_modes_boot_with_webhook_secret():
    settings = Settings(
        mode=AutomationMode.AUTO_PROFILE,
        allow_writes=True,
        seerr={"webhook_shared_secret": "s3cret"},
    )
    assert settings.allow_writes


def test_auto_mode_without_allow_writes_is_harmless():
    # the master switch is off, so the webhook cannot write regardless
    settings = Settings(mode=AutomationMode.AUTO_PROFILE, allow_writes=False)
    assert settings.mode is AutomationMode.AUTO_PROFILE


@pytest.mark.parametrize(
    "mode",
    [AutomationMode.SHADOW, AutomationMode.RECOMMEND, AutomationMode.APPROVE],
)
def test_non_auto_modes_need_no_webhook_secret(mode):
    settings = Settings(mode=mode, allow_writes=True)
    assert settings.mode is mode
