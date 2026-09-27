"""GitHub Actions에서 작품별 프로모션 값을 수동으로 저장합니다."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dashboard_history

DATA = ROOT / "data" / dashboard_history.DATA_FILENAME
HTML = ROOT / dashboard_history.HTML_FILENAME


def main() -> None:
    if not DATA.exists():
        raise SystemExit("아직 수집 데이터가 없습니다.")

    target = os.environ.get("PROMOTION_TARGET", "").strip()
    promotion = os.environ.get("PROMOTION_TEXT", "").strip()
    if not target:
        raise SystemExit("작품 ID 또는 정확한 제목을 입력해 주세요.")

    before = dashboard_history.fingerprint(DATA)
    state = json.loads(DATA.read_text(encoding="utf-8"))
    dashboard_history._validate_state(state, state.get("url"))

    books = state.get("books", {})
    matches = []
    if target in books:
        matches = [books[target]]
    else:
        matches = [b for b in books.values() if str(b.get("title", "")).strip() == target]

    if not matches:
        raise SystemExit(f"작품을 찾을 수 없습니다: {target}")
    if len(matches) > 1:
        ids = ", ".join(str(b.get("id")) for b in matches)
        raise SystemExit(f"같은 제목의 작품이 여러 개입니다. 작품 ID로 입력해 주세요: {ids}")

    book = matches[0]
    book["promotion"] = promotion
    dashboard_history.save_json(DATA, state, before, ROOT / ".backups")
    dashboard_history.render_html(HTML, state)

    print("프로모션 저장 완료")
    print("작품:", book.get("title"), f"({book.get('id')})")
    print("프로모션:", promotion if promotion else "(비움)")


if __name__ == "__main__":
    main()
