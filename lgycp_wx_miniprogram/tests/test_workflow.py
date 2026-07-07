from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "lgycp-wx-miniprogram-daily.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_runs_daily_at_beijing_1105_and_manually():
    text = workflow_text()

    assert re.search(r'cron:\s*["\']5 3 \* \* \*["\']', text)
    assert "workflow_dispatch:" in text


def test_workflow_uses_python_311_and_runs_tests_before_monitor():
    text = workflow_text()

    assert 'python-version: "3.11"' in text
    test_command = "python -m pytest lgycp_wx_miniprogram/tests -q"
    monitor_command = "python -m lgycp_wx_miniprogram.main"
    assert test_command in text
    assert monitor_command in text
    assert text.index(test_command) < text.index(monitor_command)


def test_workflow_only_commits_course_archive():
    text = workflow_text()

    git_add_lines = [line.strip() for line in text.splitlines() if "git add " in line]
    assert git_add_lines == ["git add lgycp_wx_miniprogram/data/archive.json"]


def test_workflow_injects_api_mapping_and_smtp_configuration():
    text = workflow_text()

    for name in [
        "LGYCP_WX_API_URL",
        "LGYCP_WX_API_HEADERS_JSON",
        "LGYCP_WX_ITEMS_PATH",
        "LGYCP_WX_TITLE_PATH",
        "LGYCP_WX_PUBLISHED_PATH",
        "LGPAC_NOTIFY_EMAIL",
        "LGPAC_SMTP_USER",
        "LGPAC_SMTP_PASS",
    ]:
        assert f"{name}:" in text
