#!/usr/bin/env python3
"""
Fetch today's Naver Finance research market reports and analyze them with GPT.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import ssl
import subprocess
import sys
import textwrap
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

VENDOR_DIR = Path(__file__).resolve().parent / ".vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))

import certifi
import requests
from bs4 import BeautifulSoup


BASE_URL = "https://finance.naver.com"
LIST_URL = f"{BASE_URL}/research/market_info_list.naver"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}
RETRYABLE_OPENAI_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}
DEFAULT_ICLOUD_DIR = (
    Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "증시실황"
)


@dataclass
class MarketReport:
    title: str
    broker: str
    date: str
    views: str
    url: str
    body: str = ""
    attachment_url: str = ""
    attachment_path: str = ""
    attachment_text: str = ""
    attachment_error: str = ""
    attachment_page_count: int = 0
    attachment_extracted_pages: int = 0
    attachment_text_truncated: bool = False


@dataclass
class AnalysisResult:
    text: str
    usage: dict | None = None
    error: str = ""
    used_gpt: bool = False
    prompt_truncations: list[dict] | None = None


class NaverResearchClient:
    def __init__(self, timeout: int = 20, insecure: bool = False) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.verify: bool | str = False if insecure else certifi.where()

    def get(self, url: str) -> str:
        response = self.session.get(url, timeout=self.timeout, verify=self.verify)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "euc-kr"
        return response.text

    def fetch_list_page(self, page: int) -> list[MarketReport]:
        html = self.get(f"{LIST_URL}?page={page}")
        soup = BeautifulSoup(html, "html.parser")
        reports: list[MarketReport] = []

        for row in soup.select("table tr"):
            cells = row.select("td")
            if len(cells) < 5:
                continue

            link = cells[0].select_one("a[href*='market_info_read.naver']")
            if not link:
                continue

            title = clean_text(link.get_text(" ", strip=True))
            href = link.get("href", "")
            attachment_link = cells[2].select_one("a[href]")
            attachment_url = ""
            if attachment_link:
                attachment_url = urljoin(BASE_URL + "/research/", attachment_link.get("href", ""))
            broker = clean_text(cells[1].get_text(" ", strip=True))
            date = clean_text(cells[3].get_text(" ", strip=True))
            views = clean_text(cells[4].get_text(" ", strip=True))

            reports.append(
                MarketReport(
                    title=title,
                    broker=broker,
                    date=date,
                    views=views,
                    url=urljoin(BASE_URL + "/research/", href),
                    attachment_url=attachment_url,
                )
            )

        return reports

    def fetch_report_body(self, report: MarketReport) -> MarketReport:
        html = self.get(report.url)
        soup = BeautifulSoup(html, "html.parser")
        header_node = soup.select_one("table.type_1 th")
        if header_node:
            header = clean_text(header_node.get_text(" ", strip=True))
            suffix = f" {report.broker} | {normalize_detail_date(report.date)}"
            if suffix in header:
                report.title = header.split(suffix, 1)[0].strip()

        body_node = soup.select_one("td.view_cnt")
        if body_node:
            report.body = clean_text(body_node.get_text("\n", strip=True))
        return report

    def fetch_reports_for_date(
        self,
        target: dt.date,
        max_pages: int,
        downloads_dir: Path,
        include_attachments: bool = True,
    ) -> list[MarketReport]:
        target_label = target.strftime("%y.%m.%d")
        reports: list[MarketReport] = []

        for page in range(1, max_pages + 1):
            page_reports = self.fetch_list_page(page)
            if not page_reports:
                break

            reports.extend(report for report in page_reports if report.date == target_label)

            page_dates = {report.date for report in page_reports}
            if reports and target_label not in page_dates:
                break

        for report in reports:
            self.fetch_report_body(report)
            if include_attachments and report.attachment_url:
                self.download_and_extract_attachment(report, downloads_dir / target.isoformat())

        return reports

    def download_and_extract_attachment(self, report: MarketReport, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = attachment_filename(report)
        target_path = output_dir / filename

        try:
            response = self.session.get(
                report.attachment_url,
                timeout=self.timeout,
                verify=self.verify,
            )
            response.raise_for_status()
            target_path.write_bytes(response.content)
            report.attachment_path = str(target_path)
            text, page_count, extracted_pages = extract_pdf_text_with_stats(target_path)
            report.attachment_text = text
            report.attachment_page_count = page_count
            report.attachment_extracted_pages = extracted_pages
            report.attachment_text_truncated = extracted_pages < page_count
        except Exception as exc:
            report.attachment_error = f"{type(exc).__name__}: {exc}"


def clean_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def normalize_detail_date(short_date: str) -> str:
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{2}", short_date):
        return f"20{short_date}"
    return short_date


def attachment_filename(report: MarketReport) -> str:
    match = re.search(r"/([^/?#]+\.pdf)(?:[?#].*)?$", report.attachment_url, flags=re.IGNORECASE)
    original = match.group(1) if match else "attachment.pdf"
    prefix = safe_filename(f"{report.date}_{report.broker}_{report.title}")[:90]
    return f"{prefix}_{original}"


def safe_filename(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", "_", value).strip("._ ")
    return value or "report"


def extract_pdf_text(path: Path, max_pages: int = 20) -> str:
    text, _, _ = extract_pdf_text_with_stats(path, max_pages=max_pages)
    return text


def extract_pdf_text_with_stats(path: Path, max_pages: int = 20) -> tuple[str, int, int]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "pypdf is not installed. Run `python3 -m pip install -r requirements.txt`."
        ) from exc

    reader = PdfReader(str(path))
    chunks: list[str] = []
    page_count = len(reader.pages)
    pages_to_read = min(page_count, max_pages)
    for page in reader.pages[:pages_to_read]:
        text = page.extract_text() or ""
        if text.strip():
            chunks.append(clean_text(text))
    return "\n\n".join(chunks).strip(), page_count, pages_to_read


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def build_analysis_prompt(reports: Iterable[MarketReport], target_date: dt.date) -> tuple[str, list[dict]]:
    report_blocks = []
    truncations: list[dict] = []
    for idx, report in enumerate(reports, start=1):
        body = report.body[:3500] if report.body else "(본문 없음)"
        if len(report.body) > 3500:
            truncations.append(
                {
                    "url": report.url,
                    "title": report.title,
                    "field": "body",
                    "original_chars": len(report.body),
                    "included_chars": 3500,
                }
            )
        attachment_note = ""
        if report.attachment_url:
            attachment_text = report.attachment_text[:7000] if report.attachment_text else ""
            if len(report.attachment_text) > 7000:
                truncations.append(
                    {
                        "url": report.url,
                        "title": report.title,
                        "field": "attachment_text",
                        "original_chars": len(report.attachment_text),
                        "included_chars": 7000,
                    }
                )
            if attachment_text:
                attachment_note = textwrap.dedent(
                    f"""
                    첨부 PDF: {report.attachment_url}
                    저장 위치: {report.attachment_path}
                    첨부 본문:
                    {attachment_text}
                    """
                ).strip()
            else:
                attachment_note = textwrap.dedent(
                    f"""
                    첨부 PDF: {report.attachment_url}
                    저장 위치: {report.attachment_path or "(저장 실패)"}
                    첨부 처리 오류: {report.attachment_error or "텍스트를 추출하지 못함"}
                    """
                ).strip()
        else:
            attachment_note = "첨부 PDF: 없음"

        report_blocks.append(
            textwrap.dedent(
                f"""
                [{idx}] {report.title}
                증권사: {report.broker}
                작성일: {report.date}
                URL: {report.url}
                본문:
                {body}

                {attachment_note}
                """
            ).strip()
        )

    joined = "\n\n---\n\n".join(report_blocks)
    prompt = textwrap.dedent(
        f"""
        아래 자료는 네이버 증권 리서치 탭의 시황정보 리포트 중 {target_date:%Y-%m-%d} 작성분이다.

        역할:
        - 한국어로 답한다.
        - 투자 조언이나 매수/매도 추천이 아니라, 시장 해석 리포트를 작성한다.
        - 자료 사이의 공통점과 충돌점을 분리한다.
        - 과도한 확신을 피하고, 확인이 필요한 지표를 명확히 적는다.

        출력 형식:
        # {target_date:%Y-%m-%d} 네이버 증권 시황 GPT 분석
        ## 핵심 요약
        ## 시장 분위기
        ## 상승/하락 요인
        ## 업종과 테마
        ## 리스크 체크
        ## 내일 확인할 지표
        ## 참고 리포트

        원문 자료:
        {joined}
        """
    ).strip()
    return prompt, truncations


def analyze_with_openai(
    prompt: str,
    model: str,
    prompt_truncations: list[dict] | None = None,
    retries: int = 2,
) -> AnalysisResult:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    payload = {
        "model": model,
        "input": prompt,
        "store": False,
    }
    response: requests.Response | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=90,
                verify=certifi.where(),
            )
        except requests.RequestException:
            if attempt >= retries:
                raise
            time.sleep(2**attempt)
            continue

        if response.status_code not in RETRYABLE_OPENAI_STATUS_CODES:
            break
        if attempt >= retries:
            break
        time.sleep(2**attempt)

    if response is None:
        raise RuntimeError("OpenAI API request did not return a response")
    if response.status_code >= 400:
        raise RuntimeError(format_openai_error(response))
    data = response.json()
    return AnalysisResult(
        text=extract_response_text(data),
        usage=data.get("usage"),
        used_gpt=True,
        prompt_truncations=prompt_truncations or [],
    )


def format_openai_error(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"OpenAI API error {response.status_code}: {response.text[:500]}"

    error = data.get("error") or {}
    message = error.get("message") or response.text[:500]
    code = error.get("code") or error.get("type") or "unknown_error"
    return f"OpenAI API error {response.status_code} ({code}): {message}"


def extract_response_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"].strip()

    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def analyze_locally(reports: list[MarketReport], target_date: dt.date) -> AnalysisResult:
    keyword_patterns = {
        "반도체/AI": r"반도체|AI|엔비디아|하이닉스|삼성전자|빅테크",
        "금리/채권": r"금리|채권|국채|Fed|연준|인플레|CPI|PPI",
        "환율/원화": r"환율|원화|달러|외환",
        "중국/미중": r"중국|미중|상하이|홍콩",
        "소비재": r"소비|음식료|뷰티|백화점|유통",
        "2차전지": r"2차전지|배터리|전기차",
    }
    combined = "\n".join(f"{report.title}\n{report.body}" for report in reports)
    hits = [
        name
        for name, pattern in keyword_patterns.items()
        if re.search(pattern, combined, flags=re.IGNORECASE)
    ]

    references = "\n".join(
        (
            f"- {report.title} ({report.broker}, {report.date}) - {report.url}"
            + (f" / 첨부: {report.attachment_path}" if report.attachment_path else "")
            + (f" / 첨부 오류: {report.attachment_error}" if report.attachment_error else "")
        )
        for report in reports
    )
    sample_points = "\n".join(
        f"- {report.title}: {first_sentence(report.attachment_text or report.body)}"
        for report in reports[:8]
    )
    attachment_count = sum(1 for report in reports if report.attachment_url)
    extracted_count = sum(1 for report in reports if report.attachment_text)

    text = (
        f"# {target_date:%Y-%m-%d} 네이버 증권 시황 로컬 요약\n\n"
        "## 핵심 요약\n"
        f"- 오늘 수집된 시황정보 리포트는 총 {len(reports)}건입니다.\n"
        f"- 첨부 PDF는 {attachment_count}건 발견했고, 이 중 {extracted_count}건에서 텍스트를 추출했습니다.\n"
        f"- 반복적으로 감지된 키워드는 {', '.join(hits) if hits else '뚜렷하게 감지되지 않았습니다'}입니다.\n"
        "- GPT 분석을 사용하려면 `.env`에 `OPENAI_API_KEY`를 설정한 뒤 다시 실행하세요.\n\n"
        "## 주요 포인트\n"
        f"{sample_points or '- 본문이 있는 리포트를 찾지 못했습니다.'}\n\n"
        "## 참고 리포트\n"
        f"{references}"
    ).strip()
    return AnalysisResult(text=text)


def first_sentence(text: str) -> str:
    text = clean_text(text).replace("\n", " ")
    if not text:
        return "본문 없음"
    parts = re.split(r"(?<=[.!?。])\s+|:", text, maxsplit=1)
    return parts[0][:220]


def write_outputs(
    output_dir: Path,
    target_date: dt.date,
    reports: list[MarketReport],
    analysis: AnalysisResult,
) -> tuple[Path, Path, Path]:
    stem = target_date.strftime("%Y-%m-%d")
    analysis_dir = output_dir / "analysis"
    raw_dir = output_dir / "raw"
    metadata_dir = output_dir / "metadata"
    for directory in (analysis_dir, raw_dir, metadata_dir):
        directory.mkdir(parents=True, exist_ok=True)

    markdown_path = analysis_dir / f"{stem}-market-analysis.md"
    json_path = raw_dir / f"{stem}-raw-reports.json"
    metadata_path = metadata_dir / f"{stem}-run-metadata.json"

    markdown_path.write_text(analysis.text + "\n", encoding="utf-8")
    json_path.write_text(
        json.dumps([asdict(report) for report in reports], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    metadata_path.write_text(
        json.dumps(
            {
                "date": target_date.isoformat(),
                "used_gpt": analysis.used_gpt,
                "openai_usage": analysis.usage,
                "error": analysis.error,
                "report_count": len(reports),
                "attachment_count": sum(1 for report in reports if report.attachment_url),
                "downloaded_attachment_count": sum(1 for report in reports if report.attachment_path),
                "extracted_attachment_count": sum(1 for report in reports if report.attachment_text),
                "pdf_page_truncations": [
                    {
                        "url": report.url,
                        "title": report.title,
                        "attachment_path": report.attachment_path,
                        "page_count": report.attachment_page_count,
                        "extracted_pages": report.attachment_extracted_pages,
                    }
                    for report in reports
                    if report.attachment_text_truncated
                ],
                "prompt_truncations": analysis.prompt_truncations or [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return markdown_path, json_path, metadata_path


def markdown_to_html(markdown: str, title: str) -> str:
    body_lines: list[str] = []
    in_list = False

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            if in_list:
                body_lines.append("</ul>")
                in_list = False
            level = min(len(heading.group(1)), 3)
            body_lines.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
            continue

        if line.startswith("- "):
            if not in_list:
                body_lines.append("<ul>")
                in_list = True
            body_lines.append(f"<li>{html.escape(line[2:])}</li>")
            continue

        if in_list:
            body_lines.append("</ul>")
            in_list = False
        body_lines.append(f"<p>{html.escape(line)}</p>")

    if in_list:
        body_lines.append("</ul>")

    body_html = "\n".join(body_lines)
    return textwrap.dedent(
        f"""
        <!doctype html>
        <html lang="ko">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <meta http-equiv="Cache-Control" content="no-store, no-cache, must-revalidate, max-age=0">
          <meta http-equiv="Pragma" content="no-cache">
          <meta http-equiv="Expires" content="0">
          <title>{html.escape(title)}</title>
          <style>
            :root {{
              color-scheme: light dark;
              font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
              line-height: 1.65;
              color: #1d1d1f;
              background: #fbfbfd;
            }}
            body {{
              margin: 0 auto;
              max-width: 780px;
              padding: 28px 18px 52px;
            }}
            h1, h2, h3 {{
              line-height: 1.25;
              margin: 1.4em 0 0.55em;
            }}
            h1 {{
              font-size: 1.85rem;
              margin-top: 0;
            }}
            h2 {{
              border-top: 1px solid #d2d2d7;
              font-size: 1.28rem;
              padding-top: 1em;
            }}
            p, li {{
              font-size: 1rem;
            }}
            ul {{
              padding-left: 1.25rem;
            }}
            li {{
              margin: 0.45em 0;
            }}
            @media (prefers-color-scheme: dark) {{
              :root {{
                color: #f5f5f7;
                background: #111113;
              }}
              h2 {{
                border-top-color: #3a3a3c;
              }}
            }}
          </style>
        </head>
        <body>
        {body_html}
        </body>
        </html>
        """
    ).strip()


def publish_mobile_outputs(markdown_path: Path, target_date: dt.date, mobile_dir: Path) -> Path:
    mobile_dir.mkdir(parents=True, exist_ok=True)
    stem = target_date.strftime("%Y-%m-%d")
    mobile_html_path = mobile_dir / f"{stem}-market-analysis.html"

    markdown = markdown_path.read_text(encoding="utf-8")
    write_text_atomic(mobile_html_path, markdown_to_html(markdown, f"{stem} 네이버 증권 시황 분석") + "\n")
    return mobile_html_path


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def load_seen_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(url) for url in data.get("seen_report_urls", [])}


def save_seen_urls(path: Path, urls: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "seen_report_urls": sorted(set(urls)),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def create_reminder_for_new_reports(
    reports: list[MarketReport],
    target_date: dt.date,
    markdown_path: Path,
) -> bool:
    if not reports:
        return False

    title = f"네이버 시황 새 리포트 {len(reports)}건 ({target_date:%Y-%m-%d})"
    lines = [
        f"- {report.title} ({report.broker})"
        for report in reports[:15]
    ]
    if len(reports) > 15:
        lines.append(f"- 외 {len(reports) - 15}건")
    body = "\n".join(lines)
    body += f"\n\n분석 결과: {markdown_path}"

    reminder_ok = create_reminder(title, body)
    notification_ok = display_notification(title, "Naver Market Report")
    return reminder_ok or notification_ok


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def create_output_ready_notification(
    target_date: dt.date,
    markdown_path: Path,
    metadata_path: Path,
    analysis: AnalysisResult,
) -> bool:
    result_kind = "GPT 분석" if analysis.used_gpt else "로컬 요약"
    title = f"네이버 시황 {result_kind} 완료 ({target_date:%Y-%m-%d})"
    usage = ""
    if analysis.usage:
        total_tokens = analysis.usage.get("total_tokens")
        if total_tokens is not None:
            usage = f"\nOpenAI 토큰: {total_tokens:,}"

    body = (
        f"{result_kind} 결과 파일이 생성됐습니다.\n"
        f"{markdown_path}\n\n"
        f"실행 정보: {metadata_path}"
        f"{usage}"
    )
    reminder_ok = create_reminder(title, body)
    notification_ok = display_notification(body, title)
    return reminder_ok or notification_ok


def create_reminder(title: str, body: str) -> bool:
    script = f"""
    tell application "Reminders"
        make new reminder with properties {{name:{applescript_string(title)}, body:{applescript_string(body)}}}
    end tell
    """
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"미리 알림 생성 실패: {exc.stderr.strip() or exc}", file=sys.stderr)
        return False


def display_notification(message: str, title: str) -> bool:
    script = f"display notification {applescript_string(message)} with title {applescript_string(title)}"
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"macOS 알림 표시 실패: {exc.stderr.strip() or exc}", file=sys.stderr)
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="네이버 증권 리서치 시황정보를 수집하고 GPT로 분석합니다."
    )
    parser.add_argument("--date", help="분석 날짜. 예: 2026-05-14. 기본값은 오늘")
    parser.add_argument("--max-pages", type=int, default=3, help="조회할 목록 페이지 수")
    parser.add_argument("--output-dir", default="outputs", help="결과 저장 폴더")
    parser.add_argument("--downloads-dir", default="downloads", help="첨부파일 저장 폴더")
    parser.add_argument(
        "--mobile-dir",
        default=str(DEFAULT_ICLOUD_DIR),
        help="아이폰 확인용 분석 파일을 복사할 iCloud Drive 폴더",
    )
    parser.add_argument(
        "--publish-mobile",
        dest="publish_mobile",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="분석 결과 HTML 사본을 iCloud Drive에 저장합니다",
    )
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-5"))
    parser.add_argument("--local-only", action="store_true", help="GPT 호출 없이 로컬 요약만 생성")
    parser.add_argument("--skip-attachments", action="store_true", help="첨부 PDF 다운로드와 텍스트 추출을 건너뜁니다")
    parser.add_argument("--state-file", default="state/seen-reports.json", help="새 리포트 판별용 상태 파일")
    parser.add_argument(
        "--notify-new",
        dest="notify_new",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="새 리포트가 있으면 macOS 미리 알림을 생성합니다",
    )
    parser.add_argument(
        "--notify-output",
        dest="notify_output",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="분석 결과 파일이 생성되면 macOS 미리 알림을 생성합니다",
    )
    parser.add_argument("--insecure", action="store_true", help="네이버 페이지 SSL 검증을 끕니다")
    return parser.parse_args()


def main() -> None:
    load_dotenv(Path(".env"))
    args = parse_args()
    target_date = (
        dt.datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else dt.datetime.now().date()
    )

    client = NaverResearchClient(insecure=args.insecure)
    reports = client.fetch_reports_for_date(
        target_date,
        max_pages=args.max_pages,
        downloads_dir=Path(args.downloads_dir),
        include_attachments=not args.skip_attachments,
    )
    if not reports:
        raise SystemExit(f"{target_date:%Y-%m-%d} 작성 리포트를 찾지 못했습니다.")

    state_path = Path(args.state_file)
    seen_urls = load_seen_urls(state_path)
    new_reports = [report for report in reports if report.url not in seen_urls]

    if args.local_only:
        analysis = analyze_locally(reports, target_date)
    else:
        prompt, prompt_truncations = build_analysis_prompt(reports, target_date)
        try:
            analysis = analyze_with_openai(prompt, args.model, prompt_truncations)
        except (requests.RequestException, RuntimeError, ssl.SSLError) as exc:
            analysis = analyze_locally(reports, target_date)
            analysis.prompt_truncations = prompt_truncations
            analysis.error = f"{type(exc).__name__}: {exc}"
            analysis.text += (
                "\n\n## GPT 분석 미실행\n"
                f"- 사유: {analysis.error}\n"
            )

    markdown_path, json_path, metadata_path = write_outputs(
        Path(args.output_dir), target_date, reports, analysis
    )
    mobile_html_path: Path | None = None
    if args.publish_mobile:
        try:
            mobile_html_path = publish_mobile_outputs(
                markdown_path,
                target_date,
                Path(args.mobile_dir).expanduser(),
            )
        except OSError as exc:
            print(f"아이폰용 파일 저장 실패: {exc}", file=sys.stderr)
    shared_result_path = mobile_html_path or markdown_path

    output_notification_created = False
    if args.notify_output:
        output_notification_created = create_output_ready_notification(
            target_date,
            shared_result_path,
            metadata_path,
            analysis,
        )

    reminder_created = False
    if args.notify_new and new_reports:
        reminder_created = create_reminder_for_new_reports(new_reports, target_date, shared_result_path)
    if args.notify_new and new_reports and not reminder_created:
        save_seen_urls(state_path, seen_urls)
    else:
        save_seen_urls(state_path, [*seen_urls, *(report.url for report in reports)])

    print(f"수집 리포트: {len(reports)}건")
    print(f"새 리포트: {len(new_reports)}건")
    if args.notify_new:
        print(f"미리 알림 생성: {'성공' if reminder_created else '생성 안 함'}")
    if args.notify_output:
        print(f"결과 파일 알림 생성: {'성공' if output_notification_created else '실패'}")
    print(f"분석 파일: {markdown_path}")
    print(f"원문 JSON: {json_path}")
    print(f"실행 메타데이터: {metadata_path}")
    if mobile_html_path:
        print(f"아이폰용 HTML: {mobile_html_path}")
    if analysis.usage:
        print(f"OpenAI 사용량: {analysis.usage}")


if __name__ == "__main__":
    main()
