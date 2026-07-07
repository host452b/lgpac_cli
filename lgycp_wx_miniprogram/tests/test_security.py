from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
WECHAT_APP_ID = re.compile(r"\bwx[0-9A-Za-z]{16}\b")


def test_course_monitor_does_not_embed_wechat_app_ids():
    paths = [
        *ROOT.joinpath("lgycp_wx_miniprogram").rglob("*.py"),
        *ROOT.joinpath("lgycp_wx_miniprogram").rglob("*.md"),
        *ROOT.joinpath("lgycp_wx_miniprogram").rglob("*.json"),
        ROOT / ".github" / "workflows" / "lgycp-wx-miniprogram-daily.yml",
    ]
    findings = []
    for path in paths:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if WECHAT_APP_ID.search(line):
                findings.append(f"{path.relative_to(ROOT)}:{line_number}")

    assert findings == []
