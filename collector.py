"""Public KakaoPage showcase collector. Python 3.10+, no dependencies."""
import argparse
import csv
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from xml.sax.saxutils import escape

PAGE = 'https://page.kakao.com'
API = 'https://bff-page.kakao.com'
DEFAULT_URL = PAGE + '/landing/series/list/page/landing/16140/'
UA = 'ShowcaseMetadataCollector/2.0'
KST = timezone(timedelta(hours=9))
HEADERS = ['순번', '제목', '작가명', '출판사', '공개 기준 조회수', '공개 표기 환산',
           '조회수 기준', '수집시각(KST)', '작품 ID', '작품 URL', '상태']

class CollectionError(Exception):
    pass

class HTTPFailure(CollectionError):
    def __init__(self, status, url):
        self.status = status
        super().__init__(f'HTTP {status}: {url}. 자동 재시도하지 않습니다.')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectionError('리디렉션 응답으로 중단: ' + req.full_url)

class Client:
    def __init__(self, delay=2.0):
        self.delay = max(2.0, delay)
        self.last = 0.0
        self.rules = {}
        self.opener = urllib.request.build_opener(NoRedirect())

    def raw(self, url):
        time.sleep(max(0.0, self.delay - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*',
                                     'Origin': PAGE, 'Referer': PAGE + '/'})
        try:
            with self.opener.open(req, timeout=40) as response:
                return response.read(10_000_001), response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            raise HTTPFailure(exc.code, url) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CollectionError(f'연결 실패: {url}: {exc}') from exc

    def check(self, url):
        parts = urllib.parse.urlsplit(url)
        origin = f'{parts.scheme}://{parts.netloc}'
        if origin not in (PAGE, API):
            raise CollectionError('허용한 공개 호스트 외의 요청입니다: ' + origin)
        if origin not in self.rules:
            robots_url = origin + '/robots.txt'
            try:
                body, mime = self.raw(robots_url)
            except HTTPFailure as exc:
                # RFC 9309 2.3.1.3: unavailable robots is not a disallow rule.
                # Actual data endpoint errors still stop immediately; 429 always stops.
                if not (400 <= exc.status < 500) or exc.status == 429:
                    raise
                print(f'robots.txt HTTP {exc.status}: 정책 파일 미제공. 실제 데이터 요청의 허용 여부를 별도로 확인합니다.', flush=True)
                body, mime = b'User-agent: *\nDisallow:\n', 'text/plain'
            text = body.decode('utf-8-sig', errors='replace')
            if len(body) > 10_000_000 or 'html' in mime or not re.search(r'^\s*User-agent\s*:', text, re.I | re.M):
                raise CollectionError('robots.txt 정책을 확인할 수 없어 중단: ' + robots_url)
            parser = urllib.robotparser.RobotFileParser(robots_url)
            parser.parse(text.splitlines())
            self.rules[origin] = parser
            self.delay = max(self.delay, parser.crawl_delay(UA) or 0)
            rate = parser.request_rate(UA)
            if rate and rate.requests:
                self.delay = max(self.delay, rate.seconds / rate.requests)
        if not self.rules[origin].can_fetch(UA, url):
            raise CollectionError('robots.txt가 수집을 허용하지 않는 경로입니다: ' + url)

    def get(self, path, params):
        url = API + path + '?' + urllib.parse.urlencode(params)
        self.check(url)
        body, mime = self.raw(url)
        if len(body) > 10_000_000:
            raise CollectionError('응답 크기 제한 초과')
        try:
            obj = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise CollectionError('JSON 응답이 아닙니다. 수집을 중단합니다.') from exc
        if not isinstance(obj, dict) or not isinstance(obj.get('result'), dict):
            raise CollectionError('API 응답 구조 변경 또는 오류: result 객체가 없습니다.')
        if isinstance(obj.get('result_code'), (int, float)) and obj['result_code'] < 0:
            raise CollectionError('API 오류: ' + str(obj.get('message', obj['result_code'])))
        return obj['result']

def reference(url):
    p = urllib.parse.urlsplit(url)
    prefix = '/landing/series/list/'
    if p.scheme != 'https' or p.netloc != 'page.kakao.com' or not p.path.startswith(prefix) or p.query or p.fragment:
        raise CollectionError('https://page.kakao.com/landing/series/list/... 형태의 URL을 입력하세요.')
    value = html.unescape(urllib.parse.unquote(p.path[len(prefix):].strip('/')))
    if not value or '/theme/' in value:
        raise CollectionError('테마 필터가 없는 쇼케이스 목록 주소를 입력하세요.')
    return value

def exact_count(value):
    """API 값에서 비음수 정수 조회수를 읽습니다. 이 값 자체는 저장하지 않습니다."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)', value.strip()):
        return int(value.replace(',', '').strip())
    return None


def public_count(value):
    """공개 페이지 표기 수준에 맞춰 가장 가까운 1,000회 단위로 반올림합니다.

    예: 31,500 -> 32,000, 67,600 -> 68,000.
    원 API의 더 세밀한 숫자는 반환/저장하지 않습니다.
    """
    count = exact_count(value)
    if count is not None:
        return ((count + 500) // 1000) * 1000
    if isinstance(value, str):
        text = value.strip().replace(' ', '')
        m = re.fullmatch(r'(\d+(?:\.\d+)?)만', text)
        if m:
            count = int(float(m.group(1)) * 10000 + 0.5)
            return ((count + 500) // 1000) * 1000
    return None


def public_label(count):
    if count is None:
        return ''
    if count >= 10000:
        value = count / 10000
        text = f'{value:.1f}'.rstrip('0').rstrip('.')
        return text + '만'
    return f'{count:,}'

def names(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ', '.join(dict.fromkeys(v if isinstance(v, str) else str(v.get('name', '')) for v in value if isinstance(v, (str, dict))))
    return ''

def make_row(index, item, about, collected_at):
    sid = str(item['series_id'])
    authors = names(about.get('author_list')) or names(item.get('authors'))
    publisher = (about.get('detail') or {}).get('publisher_name', '')
    raw = (item.get('service_property') or {}).get('view_count')
    count = public_count(raw)
    issues = []
    if not authors: issues.append('작가명 미제공')
    if not publisher: issues.append('출판사 미제공')
    if raw is None: issues.append('조회수 미제공')
    elif count is None: issues.append('조회수 형식 확인 필요')
    return [index, item['title'], authors, publisher, count, public_label(count),
            '공개 페이지 수준 1,000회 단위 반올림 (당일 발생량 아님)', collected_at, sid,
            PAGE + '/content/' + sid, '; '.join(issues) or '수집됨', item.get('sub_category') or item.get('category') or '']

def collect(url, delay, max_pages, log):
    ref = reference(url)
    client = Client(delay)
    client.check(url)
    items, seen, expected = [], set(), None
    for page in range(max_pages):
        log(f'목록 {page + 1}페이지 확인 중')
        result = client.get('/api/gateway/view/v1/landing/series/list',
                            {'page': page, 'size': 25, 'theme_keyword_uid': '', 'reference': ref})
        if not isinstance(result.get('list'), list) or not isinstance(result.get('is_end'), bool):
            raise CollectionError('목록 응답 구조 변경: list/is_end 확인 필요')
        if expected is None: expected = result.get('total_count')
        added = 0
        for source in result['list']:
            if not isinstance(source, dict) or not source.get('title') or not re.fullmatch(r'\d+', str(source.get('series_id', ''))):
                raise CollectionError('작품 ID/제목 응답 구조 변경')
            sid = str(source['series_id'])
            if sid in seen: continue
            seen.add(sid)
            # Store only requested public metadata, never whole detail responses.
            items.append({k: source.get(k) for k in ('series_id', 'title', 'authors', 'service_property', 'category', 'sub_category')} | {'collected_at': datetime.now(KST).isoformat(timespec='seconds')})
            added += 1
        if result['is_end']: break
        if not added: raise CollectionError('목록 반복/빈 페이지를 발견하여 중단합니다.')
    else:
        raise CollectionError('최대 페이지 수에 도달했습니다. 완전한 목록으로 저장하지 않습니다.')
    if not items: raise CollectionError('작품이 0개입니다. URL 또는 API 구조를 확인하세요.')
    if isinstance(expected, int) and expected != len(items):
        raise CollectionError(f'목록 수 불일치: 서버 {expected}개 / 수집 {len(items)}개. 다시 실행해 주세요.')
    rows = []
    for index, item in enumerate(items, 1):
        log(f"상세 {index}/{len(items)}: {item['title']}")
        about = client.get('/api/gateway/api/v1/content/about', {'series_id': item['series_id']})
        rows.append(make_row(index, item, about, item['collected_at']))
    return rows

def cell_ref(column, row):
    letters = ''
    while column:
        column, n = divmod(column - 1, 26)
        letters = chr(65 + n) + letters
    return letters + str(row)

def write_xlsx(path, rows):
    """Portable OOXML export; no Excel installation or external package needed."""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    data = [HEADERS] + rows
    sheet_rows = []
    for r, row in enumerate(data, 1):
        cells = []
        for c, value in enumerate(row, 1):
            ref = cell_ref(c, r)
            if value is None: continue
            style = '1' if r == 1 else ('2' if isinstance(value, int) else '0')
            if isinstance(value, int):
                cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
            else:
                text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))[:32767]
                cells.append(f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
        sheet_rows.append(f'<row r="{r}">' + ''.join(cells) + '</row>')
    cols = ''.join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate([7, 48, 25, 24, 23, 19, 51, 30, 16, 49, 35], 1))
    files = {
      '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
      '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
      'xl/workbook.xml': f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="쇼케이스" sheetId="1" r:id="rId1"/></sheets></workbook>',
      'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
      'xl/styles.xml': f'<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="맑은 고딕"/></font><font><b/><sz val="11"/><name val="맑은 고딕"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFE500"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0"/><xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
      'xl/worksheets/sheet1.xml': f'<worksheet xmlns="{ns}"><dimension ref="A1:K{len(data)}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="22"/><cols>{cols}</cols><sheetData>{"".join(sheet_rows)}</sheetData><autoFilter ref="A1:K{len(data)}"/></worksheet>'}
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for name, value in files.items(): archive.writestr(name, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + value)

def run(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f')
    log_path = output / f'수집로그_{stamp}.txt'
    with log_path.open('w', encoding='utf-8') as log_file:
        def log(message):
            print(message, flush=True)
            log_file.write(message + '\n'); log_file.flush()
        try:
            import history
            history.capture(output / '카카오쇼케이스.xlsx', args.url, log,
                            delay=args.delay, max_pages=args.max_pages)
            return 0
        except (CollectionError, OSError, ValueError, TypeError, KeyError) as exc:
            log('실패: ' + str(exc))
            log('전체 수집 성공을 확인할 수 없습니다. 로그를 확인해 주세요.')
            return 1

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='카카오페이지 공개 쇼케이스 → Excel')
    parser.add_argument('--url', default=DEFAULT_URL)
    parser.add_argument('--output', default='결과')
    parser.add_argument('--delay', type=float, default=2.0)
    parser.add_argument('--max-pages', type=int, default=100)
    args = parser.parse_args()
    if args.max_pages < 1 or not 2 <= args.delay <= 60:
        parser.error('--max-pages는 1 이상, --delay는 2~60초여야 합니다.')
    sys.exit(run(args))
