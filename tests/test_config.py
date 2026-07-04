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


def test_load_policy_missing_is_default_for_adhoc_use(tmp_path):
    from resolute.config import Policy, load_policy

    policy = load_policy(tmp_path / "missing.yaml")
    assert policy == Policy()


def test_load_policy_required_fails_fast_when_absent(tmp_path):
    """Production serve path: the image ships no policy file, so a missing
    file means the ConfigMap mount is broken — never silently default."""
    import pytest

    from resolute.config import load_policy

    with pytest.raises(FileNotFoundError, match="resolute-policy"):
        load_policy(tmp_path / "missing.yaml", required=True)
