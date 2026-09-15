"""workflow_dispatch 입력으로 config.json을 안전하게 갱신합니다."""
from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dashboard_history
import collector

CONFIG = ROOT / "config.json"
DATA = ROOT / "data" / dashboard_history.DATA_FILENAME
HTML = ROOT / dashboard_history.HTML_FILENAME


def main() -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    if not isinstance(cfg, dict):
        raise SystemExit("config.json 형식이 잘못되었습니다.")

    new_url = os.environ.get("NEW_URL", "").strip()
    new_start = os.environ.get("NEW_START_DATE", "").strip()
    new_end = os.environ.get("NEW_END_DATE", "").strip()

    if new_url:
        cfg["url"] = dashboard_history.normalize_url(new_url)
    else:
        cfg["url"] = dashboard_history.normalize_url(str(cfg.get("url") or collector.DEFAULT_URL))
    if new_start:
        cfg["start_date"] = new_start
    if new_end:
        cfg["end_date"] = new_end

    try:
        start = datetime.strptime(str(cfg["start_date"]), "%Y-%m-%d").date()
        end = datetime.strptime(str(cfg["end_date"]), "%Y-%m-%d").date()
    except Exception as exc:
        raise SystemExit("날짜는 YYYY-MM-DD 형식이어야 합니다.") from exc
    if start > end:
        raise SystemExit("시작일은 종료일보다 늦을 수 없습니다.")
    cfg.setdefault("delay", 2.0)
    cfg.setdefault("max_pages", 100)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if DATA.exists():
        state = json.loads(DATA.read_text(encoding="utf-8"))
        dashboard_history._validate_state(state, cfg["url"])
        state["url"] = cfg["url"]
        dashboard_history.save_json(DATA, state, dashboard_history.fingerprint(DATA), ROOT / ".backups")
        dashboard_history.render_html(HTML, state)
    else:
        dashboard_history.render_html(HTML, dashboard_history.empty(cfg["url"]))

    print("설정 저장 완료")
    print("URL:", cfg["url"])
    print("기간:", cfg["start_date"], "~", cfg["end_date"])


if __name__ == "__main__":
    main()
