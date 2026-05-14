# Naver Market Report

네이버 증권 시황정보 리포트를 매일 수집하고, 결과 HTML을 Home Assistant의 정적 파일 폴더에 저장합니다.

## 결과 보기

기본 설정에서는 결과가 아래 위치에 저장됩니다.

```text
/config/analysis/YYYY-MM-DD-market-analysis.html
```

파일 에디터나 Samba에서 `/config/analysis` 폴더를 열어 확인할 수 있습니다.

참고로 Home Assistant의 `/local/...` URL은 `/config/www` 아래 파일에만 자동으로 연결됩니다. `/config/analysis`는 파일 보관용 경로입니다.

## 설정

- `openai_api_key`: GPT 분석을 사용할 OpenAI API 키입니다. 비워두면 로컬 요약만 생성합니다.
- `model`: 사용할 OpenAI 모델입니다.
- `schedule_time`: 매일 실행할 시각입니다. `HH:MM` 형식으로 입력합니다.
- `timezone`: 실행 기준 시간대입니다. 기본값은 `Asia/Seoul`입니다.
- `max_pages`: 네이버 목록에서 확인할 페이지 수입니다.
- `output_subdir`: HTML 파일을 저장할 `/config` 아래 상대 경로입니다.
- `run_on_start`: 애드온 시작 시 즉시 한 번 실행할지 정합니다.
- `local_only`: GPT 호출 없이 로컬 요약만 생성합니다.
- `skip_attachments`: 첨부 PDF 다운로드와 텍스트 추출을 건너뜁니다.

## 설치

1. `naver_market_report` 폴더 전체를 Home Assistant의 `/addons/naver_market_report`에 복사합니다.
2. Home Assistant에서 `설정 > 애드온 > 애드온 스토어`로 이동합니다.
3. 우측 상단 메뉴에서 저장소를 새로고침합니다.
4. `로컬 애드온`에 표시되는 `Naver Market Report`를 설치합니다.
5. 설정을 입력한 뒤 시작합니다.

처음 시작하면 `run_on_start` 기본값 때문에 바로 한 번 실행합니다. 그 뒤에는 `schedule_time`에 맞춰 매일 실행합니다.
