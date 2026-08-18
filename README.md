# CANSLIM KR · 오닐 시스템 모바일 앱

한국 주식시장(KOSPI/KOSDAQ)용 CANSLIM 분석 모바일 웹 앱입니다.

## 바로 사용하기

배포 URL (GitHub Pages 활성화 후):  
**https://neotia37-art.github.io/canslim-kr-app/**

## 기능

- **시장 게이트 (M)**: 분산일·FTD·이동평균 기반 시장 상태 판정. 조정 국면이면 신규 매수 금지.
- **관심목록**: 로컬 저장, 추가/삭제 가능.
- **종목 분석**: 코드 입력 시 CANSLIM 팩터·베이스·매매계획 시각화.
- **JSON 불러오기**: 엔진 결과(`canslim_result.json`)를 붙여넣어 즉시 갱신.
- **백테스트 프레임워크**: 2015~2025 한국 데이터 기반 임계값 최적화 방향 제시.

## GitHub Pages 자동 배포 설정 (필수 1회)

1. 저장소로 이동: https://github.com/neotia37-art/canslim-kr-app
2. **Settings** → 왼쪽 **Pages**
3. **Build and deployment** → **Source** 를 **GitHub Actions** 로 선택
4. 저장 후 main 브랜치에 푸시되면 자동으로 배포됩니다.

또는 Source를 **Deploy from a branch** → Branch: `main` / Folder: `/(root)` 로 설정해도 됩니다.

## 로컬 실행

`index.html` 파일을 모바일/데스크톱 브라우저로 열면 바로 동작합니다.

## 데이터 업데이트

앱 상단의 **JSON** 버튼을 눌러 `canslim_result.json` 내용을 붙여넣으면 최신 분석 결과가 반영됩니다.
