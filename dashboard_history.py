"""JSON 누적 기록 + 설치 없이 여는 단일 HTML 대시보드.

외부 패키지는 사용하지 않습니다. 작품 ID 기준으로 수집 이력을 누적하며,
쇼케이스 URL은 중간에 변경해도 기존 이력을 유지할 수 있습니다.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import collector as c

SIGNATURE = "KAKAO_SHOWCASE_HTML_HISTORY_V1"
DATA_FILENAME = "showcase_history.json"
HTML_FILENAME = "index.html"
CONFIG_FILENAME = "config.json"
COUNT_UNIT = "exact_under_10000_rounded_1000_over"

# 이전 배포본에 함께 들어 있던 2026-09-11 최초 스냅샷의 원래 1만 이하 값.
# 직전 rounded_1000 버전에서 업데이트하는 경우에만 해당 첫 기록을 복원합니다.
LEGACY_FIRST_SNAPSHOT_TIME = "2026-09-11 21:07:10"
LEGACY_FIRST_SNAPSHOT_EXACT = {
    "70169063": 9550,
    "70339763": 8098,
    "70210371": 7293,
    "70253694": 6378,
    "70332220": 6320,
    "70206039": 5632,
    "70317162": 4915,
    "70167163": 3907,
    "70176082": 3822,
}


def normalize_url(url: str) -> str:
    return c.PAGE + "/landing/series/list/" + c.reference(url) + "/"


def empty(url: str) -> dict:
    return {
        "format": SIGNATURE,
        "url": normalize_url(url),
        "unit": COUNT_UNIT,
        "times": [],
        "books": {},
        "captures": [],
    }


def fingerprint(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _validate_state(state: dict, url: str | None = None) -> dict:
    if state.get("format") != SIGNATURE:
        raise c.CollectionError("지원하지 않는 기록 파일 형식입니다.")
    try:
        normalize_url(state.get("url", url or c.DEFAULT_URL))
        if url is not None:
            normalize_url(url)
    except Exception as exc:
        raise c.CollectionError("기록 파일의 수집 URL이 올바르지 않습니다.") from exc
    if state.get("unit") != COUNT_UNIT:
        raise c.CollectionError("조회수 단위 기록이 올바르지 않습니다.")
    times = state.get("times")
    books = state.get("books")
    captures = state.get("captures")
    if not isinstance(times, list) or not isinstance(books, dict) or not isinstance(captures, list):
        raise c.CollectionError("기록 파일 구조가 올바르지 않습니다.")
    if len(set(times)) != len(times) or not all(
        isinstance(t, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", t)
        for t in times
    ):
        raise c.CollectionError("수집 시각 기록이 올바르지 않습니다.")
    for sid, book in books.items():
        if not re.fullmatch(r"\d+", str(sid)) or not isinstance(book, dict):
            raise c.CollectionError("작품 ID 기록에 오류가 있습니다.")
        vals = book.get("values")
        if not isinstance(vals, list) or len(vals) != len(times):
            raise c.CollectionError(f"작품 {sid}의 조회수 이력 길이가 맞지 않습니다.")
        for value in vals:
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or (value > 10000 and value % 1000 != 0)
            ):
                raise c.CollectionError(
                    f"작품 {sid}의 조회수 값이 공개 표기 기준(1만 이하 1회 단위 / 1만 초과 1,000회 단위)에 맞지 않습니다."
                )
    return state


def migrate_legacy_units(state: dict) -> bool:
    """기존 기록을 새 공개 표기 기준으로 변환합니다.

    새 기준: 10,000회 이하는 1회 단위, 10,000회 초과는 1,000회 단위 반올림.
    직전 rounded_1000 버전에서 이미 반올림된 과거 1만 이하 값은 원래의 1회 단위
    숫자를 복원할 수 없으므로 그대로 유지합니다.
    """
    old_unit = state.get("unit")
    if old_unit == COUNT_UNIT:
        return False

    books = state.get("books")
    changed = False

    # 최초 버전은 '만' 단위 소수(예: 0.8098 == 8,098회)를 저장했습니다.
    # 이 경우 원래 정수를 복원한 뒤 새 공개 표기 기준을 적용할 수 있습니다.
    if old_unit in (None, "legacy_man_unit") and isinstance(books, dict):
        for book in books.values():
            vals = book.get("values") if isinstance(book, dict) else None
            if not isinstance(vals, list):
                continue
            converted = []
            for value in vals:
                if value is None:
                    converted.append(None)
                elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                    exact = int(math.floor(float(value) * 10000 + 0.5))
                    converted.append(c.public_count(exact))
                else:
                    converted.append(value)
            if converted != vals:
                book["values"] = converted
                changed = True

    # rounded_1000은 이미 정수로 변환된 기록입니다. 과거 1만 이하 원 정밀도는
    # 손실되었으므로 값은 건드리지 않고 단위 표식만 새 기준으로 올립니다.
    elif old_unit == "rounded_1000":
        # 바로 이전 배포본의 최초 번들 데이터는 원본 정밀도를 별도로 보존하고 있어 복원 가능합니다.
        times = state.get("times")
        if isinstance(books, dict) and isinstance(times, list) and times and times[0] == LEGACY_FIRST_SNAPSHOT_TIME:
            for sid, exact in LEGACY_FIRST_SNAPSHOT_EXACT.items():
                book = books.get(sid)
                vals = book.get("values") if isinstance(book, dict) else None
                if isinstance(vals, list) and vals:
                    vals[0] = exact
        changed = True

    elif old_unit not in (COUNT_UNIT,):
        raise c.CollectionError("지원하지 않는 조회수 단위 기록입니다.")

    state["unit"] = COUNT_UNIT
    return True if changed or old_unit != COUNT_UNIT else False


def load(path: Path, url: str) -> dict:
    if not path.exists():
        return empty(url)
    try:
        if path.stat().st_size > 100_000_000:
            raise c.CollectionError("기록 파일 크기가 너무 큽니다.")
        with path.open("r", encoding="utf-8") as f:
            state = json.load(f)
        migrate_legacy_units(state)
        _validate_state(state, url)
        # 월별/회차별 쇼케이스 주소를 바꿔도 기존 작품 이력은 그대로 이어 붙입니다.
        state["url"] = normalize_url(url)
        return state
    except c.CollectionError:
        raise
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise c.CollectionError("기록 파일을 읽을 수 없습니다: " + str(exc)) from exc


def append(state: dict, rows: list, when: str, mode: str = "온라인 수집") -> dict:
    if not rows:
        raise c.CollectionError("수집 작품이 0개이므로 기록하지 않습니다.")
    if when in state["times"]:
        raise c.CollectionError("같은 시각의 기록이 이미 있습니다. 중복 저장하지 않습니다.")
    if state["times"] and when <= state["times"][-1]:
        raise c.CollectionError("최근 수집 시각보다 늦은 시각만 추가할 수 있습니다.")

    state = copy.deepcopy(state)
    previous = len(state["times"])
    for book in state["books"].values():
        book["values"].append(None)
        book["status"] = "이번 목록에 없음"

    seen = set()
    for row in rows:
        sid = str(row[8])
        if sid in seen or not re.fullmatch(r"\d+", sid):
            raise c.CollectionError("수집 작품 ID 중복/오류")
        seen.add(sid)
        book = state["books"].setdefault(
            sid,
            {
                "id": sid,
                "genre": "",
                "promotion": "",
                "values": [None] * (previous + 1),
            },
        )
        count = c.public_count(row[4])
        book.update(
            title=row[1],
            authors=row[2],
            publisher=row[3],
            url=row[9],
            status=row[10],
        )
        if not book.get("genre") and len(row) > 11:
            genre = row[11]
            book["genre"] = {"현대판타지": "현판", "현대 판타지": "현판"}.get(genre, genre)
        book.setdefault("promotion", "")
        book["values"][-1] = count if count is not None else None
        if count is None:
            book["status"] = str(book["status"]) + " / 조회수 원문: " + str(row[5])

    state["times"].append(when)
    instants = [str(row[7]) for row in rows if row[7]]
    # 마지막 항목에 해당 회차의 실제 수집 URL도 보존합니다. 구버전 5열 기록도 그대로 호환됩니다.
    state["captures"].append(
        [
            when,
            mode,
            len(rows),
            min(instants) if instants else when,
            max(instants) if instants else when,
            state.get("url", ""),
        ]
    )
    return state


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.stem + "_", suffix=path.suffix + ".tmp", dir=path.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_json(path: Path, state: dict, expected_hash: str | None = None, backup_dir: Path | None = None) -> None:
    if fingerprint(path) != expected_hash:
        raise c.CollectionError("수집 도중 기록 파일이 변경되었습니다. 기존 기록을 덮어쓰지 않습니다.")
    _validate_state(state, state["url"])
    text = json.dumps(state, ensure_ascii=False, indent=2)
    if path.exists() and backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        suffix = datetime.now(c.KST).strftime("%Y%m%d_%H%M%S_%f")
        shutil.copy2(path, backup_dir / f"{path.stem}_{suffix}{path.suffix}")
    _atomic_write_text(path, text)


def _rank_map(books: list[dict]) -> dict[str, int | None]:
    values = [b["values"][-1] for b in books if b["values"] and b["values"][-1] is not None]
    result = {}
    for b in books:
        value = b["values"][-1] if b["values"] else None
        result[b["id"]] = None if value is None else 1 + sum(v > value for v in values)
    return result


def _read_config_for_html(base_dir: Path) -> dict:
    path = base_dir / CONFIG_FILENAME
    if not path.exists():
        return {}
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            return {}
        return {k: cfg.get(k) for k in ("start_date", "end_date", "url", "delay", "max_pages") if k in cfg}
    except Exception:
        return {}


def _html_payload(state: dict, base_dir: Path) -> dict:
    books = list(state["books"].values())
    ranks = _rank_map(books)
    rows = []
    for b in books:
        latest = b["values"][-1] if b["values"] else None
        prev = b["values"][-2] if len(b["values"]) > 1 else None
        delta = latest - prev if latest is not None and prev is not None else None
        rows.append(
            {
                "id": b["id"],
                "genre": b.get("genre", ""),
                "title": b.get("title", ""),
                "promotion": b.get("promotion", ""),
                "rank": ranks[b["id"]],
                "values": b.get("values", []),
                "delta": delta,
                "authors": b.get("authors", ""),
                "publisher": b.get("publisher", ""),
                "url": b.get("url", ""),
                "status": b.get("status", ""),
            }
        )
    rows.sort(key=lambda r: (r["values"][-1] is None if r["values"] else True, -(r["values"][-1] or 0) if r["values"] else 0, r["id"]))
    return {
        "times": state["times"],
        "rows": rows,
        "captures": state["captures"],
        "source": state["url"],
        "config": _read_config_for_html(base_dir),
    }


def render_html(path: Path, state: dict) -> None:
    payload = json.dumps(_html_payload(state, path.parent), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    generated = datetime.now(c.KST).strftime("%Y-%m-%d %H:%M:%S")
    doc = r'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>카카오 쇼케이스 조회수 기록</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--text:#172033;--muted:#687386;--line:#dfe5ee;--accent:#365cff;--good:#0f8a4b;--bad:#b64a3a;--sticky:#fbfcff}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font-family:Segoe UI,"Noto Sans KR","Malgun Gothic",sans-serif}
.wrap{max-width:1900px;margin:0 auto;padding:24px}.head{display:flex;gap:16px;justify-content:space-between;align-items:flex-end;flex-wrap:wrap}.head h1{font-size:26px;margin:0 0 8px}.sub{color:var(--muted);font-size:14px}.cards{display:grid;grid-template-columns:repeat(4,minmax(170px,1fr));gap:12px;margin:18px 0}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 16px;box-shadow:0 2px 8px rgba(30,45,70,.04)}.label{font-size:12px;color:var(--muted);margin-bottom:6px}.big{font-size:20px;font-weight:700}
.sourcebar,.controls{display:flex;gap:8px;flex-wrap:wrap;background:var(--card);padding:12px;border:1px solid var(--line)}.sourcebar{border-radius:12px 12px 0 0;align-items:center}.sourcebar label{font-size:13px;font-weight:700}.sourcebar input{flex:1;min-width:340px}.sourcebar input,.controls input,.controls select,.controls button,.sourcebar button{height:38px;border:1px solid #cbd4e1;border-radius:8px;background:#fff;padding:0 11px;font:inherit}.sourcebar button,.controls button{cursor:pointer}.sourcebar button.primary,.controls button.primary{background:var(--accent);border-color:var(--accent);color:#fff}.sourcehint{width:100%;font-size:12px;color:var(--muted)}.sourcehint.ok{color:var(--good)}.sourcehint.err{color:var(--bad)}
.controls{border-top:0;border-radius:0}.controls input{min-width:280px}.controls .count{margin-left:auto;align-self:center;color:var(--muted);font-size:13px}
.tablebox{overflow:auto;max-height:calc(100vh - 365px);background:#fff;border:1px solid var(--line);border-top:0;border-radius:0 0 12px 12px}.data{border-collapse:separate;border-spacing:0;min-width:max-content;width:100%;font-size:13px}.data th,.data td{padding:9px 10px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);white-space:nowrap;text-align:center}.data th{position:sticky;top:0;z-index:8;background:#edf2ff;font-weight:700}.data th.sortable{cursor:pointer;user-select:none}.data th.sortable:hover{background:#dfe7ff}.sortmark{display:inline-block;min-width:14px;margin-left:4px;color:var(--accent)}.data td.title{text-align:left;max-width:380px;overflow:hidden;text-overflow:ellipsis}.data td.muted{color:#98a1b2}.data tr:hover td{background:#f7f9ff}.data tr:hover td.sticky{background:#f7f9ff}.data a{color:#214dde;text-decoration:none}.data a:hover{text-decoration:underline}.pos{color:var(--good);font-weight:700}.neg{color:var(--bad);font-weight:700}.status-missing{color:#9a6b16}
.sticky{position:sticky;z-index:5;background:var(--sticky)}th.sticky{z-index:10;background:#e7edff}.c1{left:0;width:82px;min-width:82px;max-width:82px}.c2{left:82px;width:310px;min-width:310px;max-width:310px}.c3{left:392px;width:120px;min-width:120px;max-width:120px}.c4{left:512px;width:72px;min-width:72px;max-width:72px;box-shadow:5px 0 8px rgba(30,45,70,.06)}.title a{display:block;overflow:hidden;text-overflow:ellipsis}
.foot{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:12px;margin-top:10px}.empty{padding:40px;text-align:center;color:var(--muted)}
@media(max-width:800px){.wrap{padding:12px}.cards{grid-template-columns:repeat(2,1fr)}.controls input,.sourcebar input{min-width:180px}.tablebox{max-height:calc(100vh - 440px)}}
</style>
</head>
<body>
<div class="wrap">
  <div class="head"><div><h1>카카오 쇼케이스 조회수 기록</h1><div class="sub">날짜별 공개 조회수 누적 · 1만 이하는 1회 단위, 1만 초과는 1,000회 단위 반올림</div></div><div class="sub" id="generated"></div></div>
  <div class="cards">
    <div class="card"><div class="label">최근 수집 시각</div><div class="big" id="lastTime">-</div></div>
    <div class="card"><div class="label">이번 목록 작품</div><div class="big" id="activeCount">0</div></div>
    <div class="card"><div class="label">누적 추적 작품</div><div class="big" id="totalCount">0</div></div>
    <div class="card"><div class="label">수집 회차</div><div class="big" id="captureCount">0</div></div>
  </div>
  <div class="sourcebar">
    <label for="sourceUrl">자동수집 설정</label>
    <input id="sourceUrl" type="url" spellcheck="false" readonly>
    <button id="openSettings" class="primary">수집 링크/기간 변경</button>
    <button id="runNow">지금 한 번 수집</button>
    <span id="sourceState" class="sourcehint"></span>
  </div>
  <div class="controls">
    <input id="q" type="search" placeholder="제목 · 작가 · 출판사 검색">
    <select id="genre"><option value="">전체 장르</option></select>
    <select id="presence"><option value="all">전체 작품</option><option value="active">이번 목록에 있음</option><option value="missing">이번 목록에 없음</option></select>
    <button id="reset">필터 초기화</button>
    <button id="excel" class="primary">엑셀 다운로드 (현재 표시)</button>
    <span class="count" id="shown"></span>
  </div>
  <div class="tablebox"><table class="data"><thead id="thead"></thead><tbody id="tbody"></tbody></table><div class="empty" id="empty" hidden>조건에 맞는 작품이 없습니다.</div></div>
  <div class="foot"><span id="source"></span><span>열 제목을 클릭하면 오름차순/내림차순 정렬됩니다. 새 수집 후 브라우저에서 새로고침하면 갱신됩니다.</span></div>
</div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const DATA=JSON.parse(document.getElementById('payload').textContent);
const GENERATED='__GENERATED__';
const q=document.getElementById('q'), genre=document.getElementById('genre'), presence=document.getElementById('presence');
const sourceUrl=document.getElementById('sourceUrl'), sourceState=document.getElementById('sourceState');
let visibleRows=[];
let sortState={key:DATA.times.length?`time:${DATA.times.length-1}`:'rank',dir:'desc'};
const fmt=v=>v==null?'':Math.round(Number(v)).toLocaleString('ko-KR');
const shortTime=t=>t?`${t.slice(5,10)} ${t.slice(11,16)}`:'';
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const latest=r=>r.values.length?r.values[r.values.length-1]:null;
const genres=[...new Set(DATA.rows.map(r=>r.genre).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ko'));
for(const g of genres){const o=document.createElement('option');o.value=g;o.textContent=g;genre.appendChild(o)}
document.getElementById('generated').textContent=`HTML 생성: ${GENERATED} KST`;
document.getElementById('lastTime').textContent=DATA.times.length?DATA.times[DATA.times.length-1].slice(5):'-';
document.getElementById('activeCount').textContent=DATA.rows.filter(r=>latest(r)!=null).length.toLocaleString('ko-KR');
document.getElementById('totalCount').textContent=DATA.rows.length.toLocaleString('ko-KR');
document.getElementById('captureCount').textContent=DATA.times.length.toLocaleString('ko-KR');
document.getElementById('source').innerHTML=`현재 기록 대상: <a href="${esc(DATA.source)}" target="_blank" rel="noopener">${esc(DATA.source)}</a>`;
sourceUrl.value=(DATA.config&&DATA.config.url)||DATA.source||'';
const startDate=(DATA.config&&DATA.config.start_date)||'-', endDate=(DATA.config&&DATA.config.end_date)||'-';
sourceState.textContent=`자동수집 기간: ${startDate} ~ ${endDate} · GitHub Actions가 한국 시간 매일 11:30에 실행됩니다.`;
function githubRepoBase(){
 if(!location.hostname.endsWith('.github.io')) return null;
 const owner=location.hostname.split('.')[0];
 const parts=location.pathname.split('/').filter(Boolean);
 const repo=parts.length?parts[0]:`${owner}.github.io`;
 return `https://github.com/${owner}/${repo}`;
}
function openWorkflow(name){
 const base=githubRepoBase();
 if(!base){alert('GitHub Pages에서 열면 이 버튼으로 설정/수동 수집 화면으로 이동할 수 있습니다.');return;}
 window.open(`${base}/actions/workflows/${name}`,'_blank','noopener');
}
document.getElementById('openSettings').addEventListener('click',()=>openWorkflow('settings.yml'));
document.getElementById('runNow').addEventListener('click',()=>openWorkflow('collect.yml'));

function sortValue(r,key){
 if(key.startsWith('time:')) return r.values[Number(key.split(':')[1])] ?? null;
 if(key==='genre')return r.genre||''; if(key==='title')return r.title||''; if(key==='promotion')return r.promotion||'';
 if(key==='rank')return r.rank; if(key==='delta')return r.delta; if(key==='authors')return r.authors||'';
 if(key==='publisher')return r.publisher||''; if(key==='id')return Number(r.id); if(key==='status')return r.status||'';
 return null;
}
function compareRows(a,b){
 const av=sortValue(a,sortState.key), bv=sortValue(b,sortState.key);
 if(av==null&&bv==null)return String(a.id).localeCompare(String(b.id));
 if(av==null)return 1; if(bv==null)return -1;
 let c;
 if(typeof av==='number'&&typeof bv==='number') c=av-bv;
 else c=String(av).localeCompare(String(bv),'ko',{numeric:true,sensitivity:'base'});
 if(c===0)c=String(a.id).localeCompare(String(b.id));
 return sortState.dir==='asc'?c:-c;
}
function th(label,key,cls=''){
 const mark=sortState.key===key?(sortState.dir==='asc'?'▲':'▼'):'';
 return `<th class="sortable ${cls}" data-sort="${esc(key)}">${esc(label)}<span class="sortmark">${mark}</span></th>`;
}
function header(){
 let cells=th('장르','genre','sticky c1')+th('제목','title','sticky c2')+th('프로모션','promotion','sticky c3')+th('순위','rank','sticky c4');
 cells+=DATA.times.map((t,i)=>th(shortTime(t),`time:${i}`)).join('');
 cells+=th('증감치','delta')+th('작가명','authors')+th('출판사','publisher')+th('작품 ID','id')+th('상태','status');
 document.getElementById('thead').innerHTML='<tr>'+cells+'</tr>';
 document.querySelectorAll('th[data-sort]').forEach(el=>el.addEventListener('click',()=>{
   const key=el.dataset.sort;
   if(sortState.key===key) sortState.dir=sortState.dir==='asc'?'desc':'asc';
   else {sortState.key=key; sortState.dir=(key==='title'||key==='genre'||key==='authors'||key==='publisher'||key==='status')?'asc':'desc';}
   header(); render();
 }));
}
function filteredSortedRows(){
 const needle=q.value.trim().toLocaleLowerCase('ko-KR'), g=genre.value, p=presence.value;
 return DATA.rows.filter(r=>{
   const active=latest(r)!=null;
   const text=`${r.title} ${r.authors} ${r.publisher} ${r.id}`.toLocaleLowerCase('ko-KR');
   return (!needle||text.includes(needle))&&(!g||r.genre===g)&&(p==='all'||(p==='active'&&active)||(p==='missing'&&!active));
 }).sort(compareRows);
}
function render(){
 const rows=filteredSortedRows(); visibleRows=rows;
 const htmlRows=rows.map(r=>{
   const active=latest(r)!=null; const delta=r.delta;
   const vals=r.values.map(v=>`<td class="${v==null?'muted':''}">${v==null?'—':fmt(v)}</td>`).join('');
   const dc=delta==null?'':(delta>0?'pos':delta<0?'neg':'');
   const ds=delta==null?'—':`${delta>0?'+':''}${fmt(delta)}`;
   const stat=(r.status||(!active?'이번 목록에 없음':''));
   return `<tr><td class="sticky c1">${esc(r.genre)}</td><td class="sticky c2 title"><a href="${esc(r.url)}" target="_blank" rel="noopener" title="${esc(r.title)}">${esc(r.title)}</a></td><td class="sticky c3">${esc(r.promotion)}</td><td class="sticky c4">${r.rank??'—'}</td>${vals}<td class="${dc}">${ds}</td><td>${esc(r.authors)}</td><td>${esc(r.publisher)}</td><td>${esc(r.id)}</td><td class="${!active?'status-missing':''}">${esc(stat)}</td></tr>`;
 }).join('');
 document.getElementById('tbody').innerHTML=htmlRows;
 document.getElementById('shown').textContent=`${rows.length.toLocaleString('ko-KR')} / ${DATA.rows.length.toLocaleString('ko-KR')}개 표시`;
 document.getElementById('empty').hidden=rows.length!==0;
}

[q,genre,presence].forEach(el=>el.addEventListener(el===q?'input':'change',render));
document.getElementById('reset').addEventListener('click',()=>{q.value='';genre.value='';presence.value='all';sortState={key:DATA.times.length?`time:${DATA.times.length-1}`:'rank',dir:'desc'};header();render();q.focus()});

// ----- 외부 라이브러리 없이 XLSX(OOXML ZIP, 무압축) 생성 -----
function xmlEsc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[m])).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g,'');}
function colName(n){let s='';while(n>0){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26)}return s}
const enc=new TextEncoder();
const CRC_TABLE=(()=>{let t=new Uint32Array(256);for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=(c&1)?0xEDB88320^(c>>>1):c>>>1;t[n]=c>>>0}return t})();
function crc32(bytes){let c=0xFFFFFFFF;for(const b of bytes)c=CRC_TABLE[(c^b)&255]^(c>>>8);return (c^0xFFFFFFFF)>>>0}
function u16(n){return new Uint8Array([n&255,(n>>>8)&255])}
function u32(n){return new Uint8Array([n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255])}
function concat(parts){const len=parts.reduce((s,p)=>s+p.length,0),out=new Uint8Array(len);let o=0;for(const p of parts){out.set(p,o);o+=p.length}return out}
function dosDateTime(d=new Date()){let year=Math.max(1980,d.getFullYear());return {time:(d.getHours()<<11)|(d.getMinutes()<<5)|(d.getSeconds()>>1),date:((year-1980)<<9)|((d.getMonth()+1)<<5)|d.getDate()}}
function zipStore(files){
 const locals=[],centrals=[];let offset=0;const dt=dosDateTime();
 for(const [name,text] of Object.entries(files)){
   const nb=enc.encode(name),db=typeof text==='string'?enc.encode(text):text,crc=crc32(db);
   const local=concat([u32(0x04034b50),u16(20),u16(0x0800),u16(0),u16(dt.time),u16(dt.date),u32(crc),u32(db.length),u32(db.length),u16(nb.length),u16(0),nb,db]);
   const central=concat([u32(0x02014b50),u16(20),u16(20),u16(0x0800),u16(0),u16(dt.time),u16(dt.date),u32(crc),u32(db.length),u32(db.length),u16(nb.length),u16(0),u16(0),u16(0),u16(0),u32(0),u32(offset),nb]);
   locals.push(local);centrals.push(central);offset+=local.length;
 }
 const centralData=concat(centrals),localData=concat(locals);
 const eocd=concat([u32(0x06054b50),u16(0),u16(0),u16(centrals.length),u16(centrals.length),u32(centralData.length),u32(localData.length),u16(0)]);
 return concat([localData,centralData,eocd]);
}
function xlsxCell(value,row,col,header=false){
 const ref=colName(col)+row,style=header?' s="1"':'';
 if(value!=null&&value!==''&&typeof value==='number'&&Number.isFinite(value)) return `<c r="${ref}"${style}><v>${value}</v></c>`;
 const txt=xmlEsc(value==null?'':value).slice(0,32767);return `<c r="${ref}"${style} t="inlineStr"><is><t xml:space="preserve">${txt}</t></is></c>`;
}
function buildXlsx(rows){
 const headers=['장르','제목','프로모션','순위',...DATA.times,'증감치','작가명','출판사','작품 ID','상태','작품 URL'];
 const matrix=[headers,...rows.map(r=>[r.genre,r.title,r.promotion,r.rank,...r.values,r.delta,r.authors,r.publisher,r.id,r.status,r.url])];
 const sheetRows=matrix.map((row,ri)=>`<row r="${ri+1}">${row.map((v,ci)=>xlsxCell(v,ri+1,ci+1,ri===0)).join('')}</row>`).join('');
 const widths=[10,42,16,9,...DATA.times.map(()=>16),12,24,24,16,28,48];
 const cols=widths.map((w,i)=>`<col min="${i+1}" max="${i+1}" width="${w}" customWidth="1"/>`).join('');
 const files={
 '[Content_Types].xml':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
 '_rels/.rels':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
 'xl/workbook.xml':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="쇼케이스 기록" sheetId="1" r:id="rId1"/></sheets></workbook>',
 'xl/_rels/workbook.xml.rels':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
 'xl/styles.xml':'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
 'xl/worksheets/sheet1.xml':`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane xSplit="4" ySplit="1" topLeftCell="E2" activePane="bottomRight" state="frozen"/></sheetView></sheetViews><cols>${cols}</cols><sheetData>${sheetRows}</sheetData><autoFilter ref="A1:${colName(headers.length)}${matrix.length}"/></worksheet>`
 };
 return zipStore(files);
}
document.getElementById('excel').addEventListener('click',()=>{
 const bytes=buildXlsx(visibleRows),blob=new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),a=document.createElement('a');
 const d=new Date(),pad=n=>String(n).padStart(2,'0');
 a.href=URL.createObjectURL(blob);a.download=`카카오쇼케이스_${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}.xlsx`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1500);
});

header(); render();
</script>
</body></html>'''
    doc = doc.replace("__PAYLOAD__", payload).replace("__GENERATED__", generated)
    _atomic_write_text(path, doc)


def capture(base_dir: Path, url: str, log=print, delay: float = 2.0, max_pages: int = 100) -> tuple[Path, Path]:
    base_dir = Path(base_dir).resolve()
    data_dir = base_dir / "data"
    backup_dir = base_dir / ".backups"
    data_path = data_dir / DATA_FILENAME
    html_path = base_dir / HTML_FILENAME
    lock = data_dir / (DATA_FILENAME + ".lock")
    data_dir.mkdir(parents=True, exist_ok=True)

    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise c.CollectionError("자동/수동 수집이 이미 실행 중입니다. 실행 중인 작업이 없다면 data 폴더의 .lock 파일을 삭제하세요.") from exc
    os.close(fd)
    try:
        before = fingerprint(data_path)
        state = load(data_path, url)
        when = datetime.now(c.KST).strftime("%Y-%m-%d %H:%M:%S")
        rows = c.collect(url, delay, max_pages, log)
        state = append(state, rows, when, "온라인 수집")
        save_json(data_path, state, before, backup_dir)
        render_html(html_path, state)
        log(f"저장 완료: {data_path}")
        log(f"대시보드 갱신: {html_path}")
        log(f"수집 회차 {len(state['times'])}회 / 누적 작품 {len(state['books'])}개 / 조회수 단위 1,000회 반올림")
        return data_path, html_path
    finally:
        lock.unlink(missing_ok=True)


def change_source(base_dir: Path, url: str) -> tuple[Path | None, Path | None]:
    """설정 URL을 바꾸고, 기존 기록이 있으면 현재 대상 URL/HTML도 함께 갱신합니다."""
    base_dir = Path(base_dir).resolve()
    normalized = normalize_url(url)
    data_path = base_dir / "data" / DATA_FILENAME
    html_path = base_dir / HTML_FILENAME
    if not data_path.exists():
        return None, None
    before = fingerprint(data_path)
    state = json.loads(data_path.read_text(encoding="utf-8"))
    _validate_state(state, normalized)
    state["url"] = normalized
    save_json(data_path, state, before, base_dir / ".backups")
    render_html(html_path, state)
    return data_path, html_path


def import_state(base_dir: Path, state: dict) -> tuple[Path, Path]:
    """빌드/이관용. 기존 Excel에서 읽어 둔 state를 JSON/HTML로 한 번 옮긴다."""
    base_dir = Path(base_dir).resolve()
    state = copy.deepcopy(state)
    state["format"] = SIGNATURE
    state["url"] = normalize_url(state["url"])
    data_path = base_dir / "data" / DATA_FILENAME
    html_path = base_dir / HTML_FILENAME
    data_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(data_path, state, fingerprint(data_path), base_dir / ".backups")
    render_html(html_path, state)
    return data_path, html_path
