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


def test_load_household_missing_is_empty_for_adhoc_use(tmp_path):
    from resolute.config import HouseholdPolicy, load_household_policy

    household = load_household_policy(tmp_path / "missing.md")
    assert household == HouseholdPolicy()
    assert household.prose == ""


def test_load_household_required_fails_fast_when_absent(tmp_path):
    """Production serve path: the image ships no household file, so a missing
    file means the Secret mount is broken — never silently decide without
    the household voice (ADR-0003)."""
    import pytest

    from resolute.config import load_household_policy

    with pytest.raises(FileNotFoundError, match="resolute-household"):
        load_household_policy(tmp_path / "missing.md", required=True)


def test_household_prose_loads_and_hashes(tmp_path):
    from resolute.config import load_household_policy

    path = tmp_path / "household.md"
    path.write_text("Nature docs deserve 4K.\n")
    household = load_household_policy(path)
    assert "Nature docs" in household.prose
    assert household.source_path == str(path)
    assert len(household.content_hash) == 16
