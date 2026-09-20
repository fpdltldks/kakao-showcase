"""GitHub Actions용 자동 수집 진입점."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import collector
import dashboard_history

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
DATA = BASE / "data" / dashboard_history.DATA_FILENAME
HTML = BASE / dashboard_history.HTML_FILENAME


def read_config() -> dict:
    if not CONFIG.exists():
        raise collector.CollectionError("config.json이 없습니다.")
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError("JSON 객체가 아닙니다.")
        start = datetime.strptime(str(cfg["start_date"]), "%Y-%m-%d").date()
        end = datetime.strptime(str(cfg["end_date"]), "%Y-%m-%d").date()
        if start > end:
            raise ValueError("시작일이 종료일보다 늦습니다.")
        cfg["url"] = dashboard_history.normalize_url(str(cfg.get("url") or collector.DEFAULT_URL))
        delay = float(cfg.get("delay", 2.0))
        max_pages = int(cfg.get("max_pages", 100))
        if not 2 <= delay <= 60:
            raise ValueError("delay는 2~60초여야 합니다.")
        if max_pages < 1:
            raise ValueError("max_pages는 1 이상이어야 합니다.")
        cfg["delay"], cfg["max_pages"] = delay, max_pages
        return cfg
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise collector.CollectionError("config.json이 올바르지 않습니다: " + str(exc)) from exc


def render_existing(cfg: dict) -> None:
    if not DATA.exists():
        state = dashboard_history.empty(cfg["url"])
        dashboard_history.render_html(HTML, state)
        return
    state = json.loads(DATA.read_text(encoding="utf-8"))
    migrated = dashboard_history.migrate_legacy_units(state)
    dashboard_history._validate_state(state, cfg["url"])
    state["url"] = cfg["url"]
    if migrated:
        DATA.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print("구버전 조회수 기록을 새 공개 표기 기준으로 변환했습니다.")
    dashboard_history.render_html(HTML, state)


def already_collected_today() -> bool:
    if not DATA.exists():
        return False
    try:
        state = json.loads(DATA.read_text(encoding="utf-8"))
        today = datetime.now(collector.KST).strftime("%Y-%m-%d")
        return bool(state.get("times")) and str(state["times"][-1]).startswith(today + " ")
    except Exception:
        return False


def run(force_same_day: bool = False) -> int:
    cfg = read_config()
    now = datetime.now(collector.KST)
    today = now.date()
    start = datetime.strptime(cfg["start_date"], "%Y-%m-%d").date()
    end = datetime.strptime(cfg["end_date"], "%Y-%m-%d").date()
    print(f"현재 시각(KST): {now:%Y-%m-%d %H:%M:%S}")
    print(f"설정 기간: {start} ~ {end}")
    print(f"수집 URL: {cfg['url']}")
    if not (start <= today <= end):
        print("수집 기간 밖이므로 네트워크 수집은 건너뜁니다.")
        render_existing(cfg)
        return 0
    if already_collected_today() and not force_same_day:
        print("오늘 수집 기록이 이미 있어 중복 수집을 건너뜁니다.")
        render_existing(cfg)
        return 0
    dashboard_history.capture(
        BASE,
        cfg["url"],
        log=lambda msg: print(str(msg), flush=True),
        delay=cfg["delay"],
        max_pages=cfg["max_pages"],
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-same-day", action="store_true", help="오늘 기록이 있어도 한 번 더 수집")
    args = parser.parse_args()
    try:
        raise SystemExit(run(args.force_same_day))
    except Exception as exc:
        print("수집 실패:", exc, file=sys.stderr)
        raise SystemExit(1)
