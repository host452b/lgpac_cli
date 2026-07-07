import smtplib

import pytest

import lgpac.notify as notify_module


@pytest.fixture(autouse=True)
def email_environment(monkeypatch):
    monkeypatch.setenv("LGPAC_NOTIFY_EMAIL", "to@example.com")
    monkeypatch.setenv("LGPAC_SMTP_USER", "from@example.com")
    monkeypatch.setenv("LGPAC_SMTP_PASS", "smtp-canary")
    monkeypatch.setenv("LGPAC_SMTP_SERVER", "smtp.example.com")
    monkeypatch.setenv("LGPAC_SMTP_PORT", "465")


def fail_smtp(*args, **kwargs):
    raise smtplib.SMTPException("transport-canary")


def test_send_email_keeps_default_boolean_failure(monkeypatch):
    monkeypatch.setattr(notify_module.smtplib, "SMTP_SSL", fail_smtp)

    assert notify_module.send_email("subject", "body") is False


def test_send_email_keeps_default_missing_config_behavior(monkeypatch):
    monkeypatch.delenv("LGPAC_SMTP_PASS")

    assert notify_module.send_email("subject", "body") is False


def test_send_email_detailed_mode_preserves_exception_chain(monkeypatch):
    monkeypatch.setattr(notify_module.smtplib, "SMTP_SSL", fail_smtp)

    with pytest.raises(
        notify_module.EmailDeliveryError, match="email delivery failed"
    ) as error:
        notify_module.send_email("subject", "body", raise_on_error=True)

    assert isinstance(error.value.__cause__, smtplib.SMTPException)
    assert "transport-canary" not in str(error.value)


def test_send_email_detailed_mode_rejects_missing_config(monkeypatch):
    monkeypatch.delenv("LGPAC_SMTP_PASS")

    with pytest.raises(
        notify_module.EmailDeliveryError,
        match="email configuration is incomplete",
    ):
        notify_module.send_email("subject", "body", raise_on_error=True)
