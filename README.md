# CANSLIM KR · 오닐 시스템 모바일 앱

한국 주식시장(KOSPI/KOSDAQ)용 CANSLIM 분석 모바일 웹 앱입니다.

## 배포 URL

**https://neotia37-art.github.io/canslim-kr-app/**

(아래 설정 후 1~2분 내 활성화됩니다)

## GitHub Pages 자동 배포 설정 (1회만 하면 됩니다)

1. https://github.com/neotia37-art/canslim-kr-app 접속
2. **Settings** 탭 클릭
3. 왼쪽 사이드바에서 **Pages** 선택
4. **Build and deployment** 섹션의 **Source** 를 **GitHub Actions** 로 변경
5. 저장

이후 `main` 브랜치에 푸시할 때마다 자동으로 배포됩니다.

또는 Source를 **Deploy from a branch** → `main` / `/(root)` 로 설정해도 됩니다.

## 기능

- **시장 게이트 (M)**: 분산일 7개, FTD 미확인, 50일선 하회 → 신규 매수 금지
- **관심목록**: localStorage 저장, 추가/삭제
- **종목 분석**: 코드 입력 후 CANSLIM 점수·베이스·매매계획 표시
- **JSON 불러오기**: 엔진 결과 붙여넣기
- **백테스트 탭**: 2015~2025 한국 데이터 최적화 방향

## 주의

현재 `index.html`이 축약본입니다. 전체 기능을 사용하려면 대화에서 생성된 전체 `index.html`(약 34KB)을 이 저장소에 덮어쓰기 해주세요.

로컬에서 `index.html`을 브라우저로 열어도 바로 사용할 수 있습니다.
