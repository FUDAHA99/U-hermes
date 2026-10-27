"""Offline HTTP integration checks for the actual Recruit Edition server.

All state lives in a temporary portable root. Model calls are replaced at the
runner boundary; upload tests use the real parser and subprocess worker. No
provider request, real API key, browser, or installed Hermes engine is needed.
Run with the packaged interpreter so the PDF worker sees the shipped pypdf.
"""
import base64
import http.client
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPTS = Path(__file__).resolve().parents[1]
PORTABLE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("recruit_http_under_test", SCRIPTS / "recruit-server.py")
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)
from recruit_core import RecruitError, TASKS, extract_upload

FAKE_KEY = "test-only-not-a-real-api-key"
ANSWER = "界面测试文字：<img src=x onerror=alert(1)>；AI 建议供 HR 复核。"


def upload(name, raw):
    return {"name": name, "content": base64.b64encode(raw).decode("ascii")}


class RecruitHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="u-hermes-recruit-http-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "portable"
        shutil.copytree(PORTABLE / "recruit", self.root / "recruit")
        self.jobs = []

        def fake_runner(job):
            self.jobs.append(job)
            if job["mode"] == "extract":
                return extract_upload(job["payload"], ocr_enabled=job["ocr_enabled"])
            return {"answer": ANSWER}

        self.app = server_module.RecruitApp(self.root, runner=fake_runner)
        self.server = server_module.RecruitServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def request(self, path, payload=None, *, authenticated=True, headers=None, raw=None):
        request_headers = {"X-Recruit-Token": self.app.token} if authenticated else {}
        body = None
        if payload is not None or raw is not None:
            body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request("POST" if body is not None else "GET", path, body=body, headers=request_headers)
            response = connection.getresponse()
            text = response.read().decode("utf-8")
            data = json.loads(text) if "application/json" in response.getheader("Content-Type", "") else text
            return response.status, data, dict(response.getheaders())
        finally:
            connection.close()

    def configure(self, **updates):
        status, data, _ = self.request("/api/config", updates)
        self.assertEqual(status, 200, data)
        return data

    def question(self):
        return {"task": "qa", "prompt": "新媒体运营几点上下班？", "jd": "", "resumes": []}

    def assert_no_usage(self):
        self.assertEqual({task: self.app.usage.snapshot()[task] for task in TASKS},
                         {task: 0 for task in TASKS})

    def test_health_and_page_token_substitution(self):
        status, data, _ = self.request("/health", authenticated=False)
        self.assertEqual((status, data), (200, {"edition": "recruit"}))
        status, html, headers = self.request("/", authenticated=False)
        self.assertEqual(status, 200)
        self.assertNotIn("__RECRUIT_TOKEN__", html)
        self.assertIn(self.app.token, html)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/missing")[0], 404)

    def test_config_requires_token_and_never_returns_key(self):
        self.assertEqual(self.request("/api/config", authenticated=False)[0], 403)
        self.assertEqual(self.request("/api/config", headers={"X-Recruit-Token": "wrong"})[0], 403)
        public = self.configure(api_key=FAKE_KEY)
        self.assertTrue(public["has_api_key"])
        self.assertNotIn("api_key", public)
        status, public, _ = self.request("/api/config")
        self.assertEqual(status, 200)
        self.assertNotIn(FAKE_KEY, json.dumps(public))
        self.assertEqual(public["model"], "deepseek-flash")
        self.assertFalse(public["allow_tools"])
        self.assertEqual(public["limits"]["screen"], 300)
        self.assertEqual(public["limits"]["qa"], 800)

    def test_save_config_round_trip_preserves_blank_key_and_general_edition(self):
        general = self.root / "data" / "config.yaml"
        general.write_text("# general edition stays byte-identical\n", encoding="utf-8")
        before = general.read_bytes()
        self.configure(api_key=FAKE_KEY)
        limits = {"screen": 7, "qa": 11, "jd": 3, "interview": 4}
        public = self.configure(api_key="", company_info="青禾智选（虚构），双休。",
                                model="deepseek-flash", limits=limits)
        self.assertEqual(public["limits"], limits)
        self.assertEqual(public["company_info"], "青禾智选（虚构），双休。")
        self.assertEqual(self.app.store.key(), FAKE_KEY)
        self.assertEqual(general.read_bytes(), before)
        self.assertNotIn(FAKE_KEY, json.dumps(public))

    def test_forged_origins_hosts_and_tokens_cannot_mutate(self):
        before = (self.app.store.data / "config.json").read_bytes()
        for headers in ({"Origin": "https://untrusted.example"},
                        {"Origin": "null"},
                        {"Host": "untrusted.example:" + str(self.server.server_port)},
                        {"X-Recruit-Token": "wrong"}):
            with self.subTest(headers=headers):
                status, data, _ = self.request("/api/config", {"api_key": FAKE_KEY}, headers=headers)
                self.assertEqual(status, 403, data)
        self.assertFalse((self.app.store.data / "secrets.json").exists())
        self.assertEqual((self.app.store.data / "config.json").read_bytes(), before)
        self.assertEqual(self.request("/", headers={"Host": "untrusted.example"})[0], 403)
        self.assertEqual(self.request("/api/config", headers={"Origin": "https://untrusted.example"})[0], 403)
        status, _, _ = self.request("/api/config", {},
                                    headers={"Origin": "http://127.0.0.1:" + str(self.server.server_port)})
        self.assertEqual(status, 200)

    def test_browser_cannot_enable_tools_or_add_unknown_configuration(self):
        before = (self.app.store.data / "config.json").read_bytes()
        for payload in ({"allow_tools": True}, {"api_key": FAKE_KEY, "unknown": "value"},
                        {"max_tokens": 8000}):
            with self.subTest(payload=payload):
                status, data, _ = self.request("/api/config", payload)
                self.assertEqual(status, 400, data)
                self.assertIn("error", data)
        self.assertEqual((self.app.store.data / "config.json").read_bytes(), before)
        self.assertEqual(self.app.store.key(), "")

    def test_missing_key_rejects_before_quota_or_runner(self):
        status, data, _ = self.request("/api/run", self.question())
        self.assertEqual(status, 400)
        self.assertIn("API Key", data["error"])
        self.assertEqual(self.jobs, [])
        self.assert_no_usage()

    def test_question_and_five_resume_batch_charge_actual_units(self):
        self.configure(api_key=FAKE_KEY)
        status, answer, _ = self.request("/api/run", self.question())
        self.assertEqual(status, 200, answer)
        self.assertEqual(answer["answer"], ANSWER)
        self.assertEqual(answer["usage"]["qa"], 1)
        payload = {"task": "screen", "prompt": "按岗位评分并排序", "jd": "新媒体运营，内容策划与数据复盘。",
                   "resumes": [{"name": "虚构候选人%d.txt" % n, "text": "虚构简历：有相关项目经验。"} for n in range(5)]}
        status, answer, _ = self.request("/api/run", payload)
        self.assertEqual(status, 200, answer)
        self.assertEqual(answer["usage"]["screen"], 5)
        self.assertEqual(answer["usage"]["qa"], 1)
        self.assertEqual(len(self.jobs), 2)
        self.assertEqual(len(json.loads(self.jobs[-1]["user"])["resumes"]), 5)
        self.assertFalse(self.jobs[-1]["config"]["allow_tools"])
        self.assertIn("青禾智选", self.jobs[-1]["system"])

    def test_exhausted_quota_never_dispatches_and_restart_retains_count(self):
        self.configure(api_key=FAKE_KEY, limits={"screen": 1, "qa": 1, "jd": 0, "interview": 0})
        self.assertEqual(self.request("/api/run", self.question())[0], 200)
        status, data, _ = self.request("/api/run", self.question())
        self.assertEqual(status, 400)
        self.assertIn("额度不足", data["error"])
        self.assertEqual(len(self.jobs), 1)
        restarted = server_module.RecruitApp(self.root, runner=lambda _: self.fail("must not dispatch"))
        self.assertEqual(restarted.usage.snapshot()["qa"], 1)
        with self.assertRaises(RecruitError):
            restarted.run(self.question())

    def test_failed_provider_attempt_stays_counted_and_releases_busy_lock(self):
        self.configure(api_key=FAKE_KEY)

        def fail(job):
            self.jobs.append(job)
            raise RecruitError("模拟模型失败，请稍后重试。")

        self.app.runner = fail
        status, data, _ = self.request("/api/run", self.question())
        self.assertEqual(status, 400)
        self.assertIn("模拟模型失败", data["error"])
        self.assertEqual(self.app.usage.snapshot()["qa"], 1)
        self.assertFalse(self.app.busy.locked())
        self.app.runner = lambda _: {"answer": "恢复成功"}
        self.assertEqual(self.request("/api/run", self.question())[0], 200)
        self.assertEqual(self.app.usage.snapshot()["qa"], 2)

    def test_busy_request_rejected_without_charging_or_dispatch(self):
        self.configure(api_key=FAKE_KEY)
        self.app.busy.acquire()
        try:
            status, data, _ = self.request("/api/run", self.question())
            self.assertEqual(status, 400)
            self.assertIn("上一项任务", data["error"])
        finally:
            self.app.busy.release()
        self.assertEqual(self.jobs, [])
        self.assert_no_usage()

    def test_malformed_requests_do_not_reach_model(self):
        self.configure(api_key=FAKE_KEY)
        for payload in ({"task": "terminal", "prompt": "whoami"},
                        dict(self.question(), allow_tools=True),
                        dict(self.question(), resumes=[{"name": "a.txt", "text": "x"}]),
                        {"task": "screen", "prompt": "打分", "jd": "岗位", "resumes": []}):
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/api/run", payload)[0], 400)
        self.assertEqual(self.request("/api/run", raw=b"{")[0], 400)
        self.assertEqual(self.request("/api/run", raw=b"{}", headers={"Content-Type": "text/plain"})[0], 400)
        self.assertEqual(self.request("/api/run", raw=b"{}",
                                      headers={"Content-Length": str(server_module.MAX_BODY + 1)})[0], 400)
        self.assertEqual(self.jobs, [])
        self.assert_no_usage()

    def test_upload_names_are_labels_and_cannot_read_or_write_local_paths(self):
        sentinel = self.root / "private.txt"
        sentinel.write_text("DO-NOT-READ-LOCAL-CONTENT", encoding="utf-8")
        safe_text = "只解析上传内容，不读取文件名指向的路径。"
        for name in (str(sentinel), "../../private.txt", "C:\\private\\resume.txt"):
            with self.subTest(name=name):
                status, data, _ = self.request("/api/extract", upload(name, safe_text.encode("utf-8")))
                self.assertEqual(status, 200, data)
                self.assertEqual(data["text"], safe_text)
                self.assertNotIn("DO-NOT-READ", json.dumps(data))
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "DO-NOT-READ-LOCAL-CONTENT")
        status, data, _ = self.request("/api/extract", {"path": str(sentinel)})
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        self.assert_no_usage()

    def test_invalid_uploads_are_friendly_errors_without_tracebacks(self):
        cases = [upload("resume.exe", b"fake executable"), upload("resume.docx", b"not a zip"),
                 upload("resume.txt", b"\xff\xfe"), upload("empty.txt", b""),
                 {"name": "resume.txt", "content": "not base64!!"},
                 {"name": "resume.txt", "content": "eA==", "path": "/etc/passwd"}]
        for payload in cases:
            with self.subTest(name=payload.get("name")):
                status, data, _ = self.request("/api/extract", payload)
                self.assertEqual(status, 400, data)
                self.assertIsInstance(data["error"], str)
                self.assertNotIn("Traceback", data["error"])
                self.assertNotIn(FAKE_KEY, data["error"])
        self.assert_no_usage()

    def test_real_docx_worker_extracts_and_removes_temporary_runtime(self):
        self.app.runner = self.app.worker
        paths = sorted((PORTABLE / "recruit/demo-resumes").glob("*.docx"))
        self.assertEqual(len(paths), 2)
        for path in paths:
            with self.subTest(name=path.name):
                status, data, _ = self.request("/api/extract", upload(path.name, path.read_bytes()))
                self.assertEqual(status, 200, data)
                self.assertIn(path.stem.split("_")[1], data["text"])
                self.assertIn("虚构", data["text"])
        self.assertEqual(list((self.app.store.data / "runs").iterdir()), [])
        self.assert_no_usage()

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "PDF worker requires packaged pypdf; run with bundled interpreter")
    def test_real_pdf_worker_extracts_all_three_demo_resumes(self):
        self.app.runner = self.app.worker
        paths = sorted((PORTABLE / "recruit/demo-resumes").glob("0[1-5]_*.pdf"))
        self.assertEqual(len(paths), 3)
        for path in paths:
            with self.subTest(name=path.name):
                status, data, _ = self.request("/api/extract", upload(path.name, path.read_bytes()))
                self.assertEqual(status, 200, data)
                self.assertIn(path.stem.split("_")[1], data["text"])
                self.assertIn("虚构", data["text"])
        self.assertEqual(list((self.app.store.data / "runs").iterdir()), [])
        self.assert_no_usage()

    def test_page_does_not_treat_model_or_filename_text_as_html(self):
        # These are untrusted model/document strings. Keep the rendering sinks
        # text-only even if a future UI change adds Markdown presentation.
        _, html, _ = self.request("/")
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("insertAdjacentHTML", html)
        self.assertIn("$('output').textContent = result.answer", html)
        self.assertIn("name.textContent = resume.name", html)
        self.assertNotIn("localStorage", html)
        self.assertNotIn("sessionStorage", html)


if __name__ == "__main__":
    unittest.main()
