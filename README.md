# 카카오 쇼케이스 자동수집 · GitHub Actions + Pages 버전

이 저장소는 PC를 켜 두지 않아도 GitHub 서버에서 카카오페이지 쇼케이스 공개 데이터를 정해진 기간 동안 매일 수집하고, 결과를 GitHub Pages 웹페이지로 보여 주는 버전입니다.

## 핵심 기능

- 한국 시간 **매일 10:00** GitHub Actions 자동 실행
- PC 전원이 꺼져 있어도 GitHub 서버에서 실행
- 시작일/종료일 밖에서는 실제 데이터 수집을 건너뜀
- 쇼케이스 URL 변경 가능
- 기존 누적 기록 유지
- 공개 페이지 표기 수준에 맞춰 조회수를 **1,000회 단위로 반올림**해 저장하고 증감 계산
- 웹페이지에서 검색/필터
- 모든 열 오름차순/내림차순 정렬
- 현재 표시 데이터 `.xlsx` 다운로드
- 웹페이지의 **수집 링크/기간 변경**, **지금 한 번 수집** 버튼으로 GitHub 작업 화면 이동

> GitHub의 예약 작업은 한국 시간 10:00에 예약되어 있습니다. GitHub 서버 상황에 따라 실제 시작이 몇 분 늦어질 수 있습니다.

## 처음 설치하는 방법

자세한 설명은 `GitHub_설정_가이드.txt`를 보세요. 핵심 순서는 아래와 같습니다.

1. GitHub에서 새 **Public** repository를 만듭니다. 이름은 `kakao-showcase`를 추천합니다.
2. 이 폴더의 **내용물 전체**를 repository에 업로드합니다. ZIP 파일 자체를 올리는 것이 아닙니다.
3. repository의 `Settings` → `Pages` → `Source`에서 **GitHub Actions**를 선택합니다.
4. `Actions` 탭 → **수집 설정 변경** → `Run workflow`를 누릅니다.
5. URL, 시작일, 종료일을 입력하고 실행합니다.
6. 성공 후 `Settings` → `Pages` → `Visit site`를 누르면 결과 페이지가 열립니다.

예상 주소는 다음과 같습니다.

`https://내-GitHub-아이디.github.io/kakao-showcase/`

## 설정 변경

웹페이지의 **수집 링크/기간 변경** 버튼을 누르거나 GitHub에서 다음으로 이동합니다.

`Actions` → `수집 설정 변경` → `Run workflow`

입력값:

- `url`: 새 쇼케이스 URL. 바꾸지 않으면 비워 둡니다.
- `start_date`: `YYYY-MM-DD`
- `end_date`: `YYYY-MM-DD`
- `collect_now`: 설정 저장 후 오늘 데이터도 수집할지 여부

## 지금 한 번 수집

웹페이지의 **지금 한 번 수집** 버튼 또는

`Actions` → `매일 오전 10시 데이터 수집` → `Run workflow`

를 사용합니다.

같은 날 이미 한 번 수집했다면 기본적으로 중복 수집하지 않습니다. 같은 날 두 번째 스냅샷이 꼭 필요하면 `force_same_day`를 체크합니다.

## 주요 파일

- `config.json`: 현재 수집 URL 및 기간
- `data/showcase_history.json`: 누적 데이터
- `index.html`: 결과 대시보드
- `.github/workflows/collect.yml`: 매일 10:00 자동 수집
- `.github/workflows/settings.yml`: URL/기간 변경
- `cloud_collect.py`: GitHub용 수집 진입점

## 공개 범위

GitHub Free에서 비용 없이 GitHub Pages를 쓰는 가장 단순한 구성은 **Public repository**입니다. 이 경우 repository의 코드와 `data/showcase_history.json`, 결과 웹페이지는 인터넷에 공개됩니다. 개인 정보나 비공개 데이터를 넣지 마세요.
