import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

import naver_market_report as module


def item(nid="37491", **overrides):
    return {
        "nid": nid,
        "title": "데일리 &amp; 전망",
        "brokerName": "테스트증권",
        "writeDate": "2026-09-11",
        "readCount": "24",
        "content": "<p>시장 전망</p><p>상승 &amp; 하락<br>변동성</p>",
        **overrides,
    }


class NaverResearchTests(unittest.TestCase):
    def setUp(self):
        self.client = module.NaverResearchClient()
        self.client.session = Mock()
        self.target = dt.date(2026, 9, 11)

    def responses(self, *payloads):
        responses = []
        for payload in payloads:
            response = Mock()
            response.json.return_value = payload
            responses.append(response)
        self.client.session.get.side_effect = responses

    def test_api_fields_and_html(self):
        report = module.report_from_api(item())
        self.assertEqual(report.title, "데일리 & 전망")
        self.assertEqual(report.broker, "테스트증권")
        self.assertEqual(report.date, "26.09.11")
        self.assertEqual(report.views, "24")
        self.assertEqual(report.body, "시장 전망\n상승 & 하락\n변동성")
        self.assertEqual(report.url, f"{module.LIST_URL}/37491")
        self.assertEqual(report.attachment_url, "")

    def test_page_indexes_and_date_filter(self):
        self.responses({"items": [item()], "hasNext": True})
        self.client.fetch_list_page(2, target=self.target)
        self.assertEqual(self.client.session.get.call_args.kwargs["params"], {
            "index": 1, "size": 15, "startDate": "2026-09-11", "endDate": "2026-09-11",
        })
        self.assertTrue(self.client.has_next_page)

    def test_multiple_pages_deduplicate_and_stop_at_last_page(self):
        self.responses(
            {"items": [item()], "hasNext": True},
            {"items": [item(), item("37490"), item("37489", writeDate="2026-09-10")], "hasNext": False},
            item(), item("37490"),
        )
        self.client.download_and_extract_attachment = Mock()
        reports = self.client.fetch_reports_for_date(self.target, 5, Path("unused"), False)
        self.assertEqual([r.url for r in reports], [f"{module.LIST_URL}/37491", f"{module.LIST_URL}/37490"])
        self.assertEqual(self.client.session.get.call_count, 4)
        self.client.download_and_extract_attachment.assert_not_called()

    def test_detail_attachment_is_used_for_download(self):
        attachment = "https://stock.pstatic.net/stock-research/market/58/report.pdf"
        self.responses({"items": [item()], "hasNext": False}, item(attachUrl=attachment))
        self.client.download_and_extract_attachment = Mock()
        reports = self.client.fetch_reports_for_date(self.target, 3, Path("downloads"))
        self.assertEqual(reports[0].attachment_url, attachment)
        self.client.download_and_extract_attachment.assert_called_once_with(reports[0], Path("downloads/2026-09-11"))

    def test_max_pages_and_skip_attachments(self):
        self.responses({"items": [item()], "hasNext": True}, item(attachUrl="https://stock.pstatic.net/report.pdf"))
        self.client.download_and_extract_attachment = Mock()
        reports = self.client.fetch_reports_for_date(self.target, 1, Path("unused"), False)
        self.assertEqual(len(reports), 1)
        self.assertEqual(self.client.session.get.call_count, 2)
        self.client.download_and_extract_attachment.assert_not_called()

    def test_no_reports(self):
        self.responses({"items": [], "hasNext": False})
        self.assertEqual(self.client.fetch_reports_for_date(self.target, 3, Path("unused")), [])
        self.assertEqual(self.client.session.get.call_count, 1)

    def test_invalid_responses_are_not_treated_as_no_reports(self):
        for payload in [[], {"error": "unavailable"}, {"items": [], "hasNext": "false"}, {"items": [{}], "hasNext": False}]:
            with self.subTest(payload=payload):
                self.responses(payload)
                with self.assertRaises(RuntimeError):
                    self.client.fetch_list_page(1)

    def test_http_error_propagates(self):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("503")
        self.client.session.get.return_value = response
        with self.assertRaises(requests.HTTPError):
            self.client.fetch_list_page(1)
        response.json.assert_not_called()

    def test_wrong_detail_id_is_rejected(self):
        self.responses(item("99999"))
        with self.assertRaises(RuntimeError):
            self.client.fetch_report_body(module.report_from_api(item()))

    def test_optional_fields_can_be_null(self):
        report = module.report_from_api(item(content=None, attachUrl=None, readCount=None))
        self.assertEqual((report.body, report.attachment_url, report.views), ("", "", "0"))

    def test_legacy_seen_urls_migrate_without_duplicate_notifications(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "seen.json"
            path.write_text(json.dumps({"seen_report_urls": [
                "https://finance.naver.com/research/market_info_read.naver?nid=37491&page=1",
                "https://finance.naver.com/research/market_info_read.naver?page=2&nid=37491",
                f"{module.LIST_URL}/37490",
            ]}))
            self.assertEqual(module.load_seen_urls(path), {f"{module.LIST_URL}/37491", f"{module.LIST_URL}/37490"})


if __name__ == "__main__":
    unittest.main()
