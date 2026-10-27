"""Recruit configuration, real document parsing and durable budget regressions."""
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from recruit_core import (ConfigStore, MAX_FILE, RecruitError, Usage, extract_upload,
                          prompts, validate_config, validate_request)
from recruit_worker import run_agent_job

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ASSETS = SCRIPTS.parent / "recruit"
SERVER_SPEC = importlib.util.spec_from_file_location("recruit_server_core_tests", SCRIPTS / "recruit-server.py")
server = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(server)


def upload(name, raw):
    return {"name": name, "content": base64.b64encode(raw).decode("ascii")}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ASSETS, self.root / "recruit")
        self.store = ConfigStore(self.root)
        self.store.initialize()
        self.usage = Usage(self.store.data / "usage.sqlite3", clock=lambda: "2026-10")

    def tearDown(self):
        self.temp.cleanup()

    def test_defaults_and_skills_install(self):
        cfg = self.store.load()
        self.assertEqual(cfg["model"], "deepseek-flash")
        self.assertFalse(cfg["allow_tools"])
        self.assertEqual(cfg["limits"], {"screen": 300, "qa": 800, "jd": 100, "interview": 100})
        self.assertEqual(len(list((self.store.data / "hermes-home/skills").glob("*/SKILL.md"))), 4)

    def test_customer_edits_survive_reinitialization_and_general_data_untouched(self):
        general = self.root / "data/config.yaml"
        general.write_bytes(b"model: customer-general\r\n")
        self.store.save({"company_info": "示例客户资料", "api_key": "fixture-key-not-a-real-key"})
        self.store.initialize()
        self.assertEqual(self.store.company(), "示例客户资料")
        self.assertEqual(self.store.key(), "fixture-key-not-a-real-key")
        self.assertEqual(general.read_bytes(), b"model: customer-general\r\n")

    def test_public_never_returns_key_and_blank_preserves_it(self):
        self.store.save({"api_key": "fixture-key-not-a-real-key"})
        self.store.save({"api_key": "", "company_info": "新公司"})
        public = self.store.public(self.usage)
        self.assertTrue(public["has_api_key"])
        self.assertNotIn("fixture-key-not-a-real-key", json.dumps(public))
        self.assertEqual(self.store.key(), "fixture-key-not-a-real-key")

    def test_invalid_settings_do_not_write_and_tool_switch_is_file_only(self):
        path = self.store.data / "config.json"
        original = path.read_bytes()
        for update in ({"allow_tools": True}, {"model": "other-provider"}, {"limits": {"qa": -1}},
                       {"api_key": "key\nINJECT=1"}, {"company_info": " "}):
            with self.subTest(update=update), self.assertRaises(RecruitError):
                self.store.save(update)
            self.assertEqual(path.read_bytes(), original)
        cfg = json.loads(original)
        cfg["allow_tools"] = True
        path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertTrue(self.store.load()["allow_tools"])
        cfg["allow_tools"] = "false"
        with self.assertRaises(RecruitError):
            validate_config(cfg)
        cfg["allow_tools"] = False
        cfg["api_key"] = "fixture-not-real"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaises(RecruitError):
            self.store.public(self.usage)

    def test_corrupt_config_not_silently_reset(self):
        path = self.store.data / "config.json"
        path.write_bytes(b"{torn")
        with self.assertRaises(RecruitError):
            self.store.initialize()
        self.assertEqual(path.read_bytes(), b"{torn")

    def test_counter_persistence_batch_limit_and_rollover(self):
        self.usage.reserve("screen", 5, 6)
        reopened = Usage(self.usage.path, clock=lambda: "2026-10")
        self.assertEqual(reopened.snapshot()["screen"], 5)
        with self.assertRaises(RecruitError):
            reopened.reserve("screen", 2, 6)
        reopened.reserve("screen", 1, 6)
        with self.assertRaises(RecruitError):
            reopened.reserve("qa", 1, 0)
        november = Usage(self.usage.path, clock=lambda: "2026-11")
        self.assertEqual(november.snapshot()["screen"], 0)
        november.reserve("screen", 1, 6)
        self.assertEqual(reopened.snapshot()["screen"], 6)

    def test_concurrent_counter_never_overspends(self):
        def reserve(_):
            try:
                Usage(self.usage.path, clock=lambda: "2026-10").reserve("qa", 1, 7)
                return True
            except RecruitError:
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(reserve, range(30)))
        self.assertEqual(sum(outcomes), 7)
        self.assertEqual(self.usage.snapshot()["qa"], 7)

    def test_independent_processes_share_atomic_budget(self):
        code = "import sys;sys.path.insert(0,sys.argv[1]);from recruit_core import Usage,RecruitError\ntry: Usage(sys.argv[2],clock=lambda:'2026-10').reserve('qa',1,3)\nexcept RecruitError: sys.exit(2)"
        jobs = [subprocess.Popen([sys.executable, "-c", code, str(SCRIPTS), str(self.usage.path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(8)]
        for job in jobs:
            job.communicate(timeout=30)
        self.assertEqual(sum(job.returncode == 0 for job in jobs), 3)
        self.assertTrue(all(job.returncode in (0, 2) for job in jobs))
        self.assertEqual(self.usage.snapshot()["qa"], 3)

    def test_corrupt_usage_fails_closed(self):
        self.usage.path.write_bytes(b"broken database")
        with self.assertRaises(sqlite3.DatabaseError):
            Usage(self.usage.path)
        self.assertEqual(self.usage.path.read_bytes(), b"broken database")

    def test_validation_does_not_trust_client_counts_or_paths(self):
        base = {"task": "screen", "prompt": "打分", "jd": "运营", "resumes": [{"name": "a", "text": "运营经历"}]}
        self.assertEqual(len(validate_request(base)["resumes"]), 1)
        for changes in ({"count": 0}, {"resumes": []}, {"resumes": base["resumes"] * 6},
                        {"resumes": [{"name": "a", "path": "/etc/passwd"}]},
                        {"jd": ""}, {"task": []}, {"task": "qa"}, {"prompt": "x" * 10001}):
            with self.subTest(changes=changes), self.assertRaises(RecruitError):
                validate_request(dict(base, **changes))

    def test_prompt_routes_exact_skill_and_company(self):
        self.store.save({"company_info": "可核实公司信息：月薪12345"})
        request = validate_request({"task": "qa", "prompt": "薪资？"})
        system, user = prompts(self.store, request)
        self.assertIn("月薪12345", system)
        self.assertIn("name: recruit-qa", system)
        self.assertNotIn("name: recruit-screen", system)
        self.assertEqual(json.loads(user)["prompt"], "薪资？")

    def test_five_real_demo_files_extract(self):
        for path in sorted((ASSETS / "demo-resumes").iterdir()):
            if path.suffix in (".pdf", ".docx") and path.name[:2] in {"01", "02", "03", "04", "05"}:
                with self.subTest(file=path.name):
                    result = extract_upload(upload(path.name, path.read_bytes()))
                    self.assertIn(path.stem.split("_")[1], result["text"])
                    self.assertIn("虚构", result["text"])

    def test_upload_name_cannot_read_or_write_path(self):
        result = extract_upload(upload("../../secret.txt", "我是正文".encode()))
        self.assertEqual(result["text"], "我是正文")
        self.assertFalse((self.root / "secret.txt").exists())

    def test_upload_validation(self):
        for payload in (upload("test.exe", b"abc"), upload("a.txt", b""),
                        upload("a.txt", b"x" * (MAX_FILE + 1)),
                        upload("a.pdf", b"bad pdf"), upload("a.docx", b"bad zip"),
                        {"name": "a.txt", "content": "not base64"},
                        upload("a.txt", b"\xff\xff")):
            with self.subTest(name=payload["name"]), self.assertRaises(RecruitError):
                extract_upload(payload)

    def test_docx_compressed_expansion_and_entity_rejected(self):
        for xml in (b"x" * (2 * 1024 * 1024 + 1), b'<!DOCTYPE doc [<!ENTITY x "bad">]><doc>&x;</doc>'):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("word/document.xml", xml)
            with self.assertRaises(RecruitError):
                extract_upload(upload("bad.docx", buffer.getvalue()))

    def test_host_environment_cannot_enable_plugins_or_inject_credentials(self):
        with patch.dict(os.environ, {"HERMES_HOME": "/host", "HERMES_SAFE_MODE": "0",
                                    "HERMES_MANAGED_DIR": "/etc/hermes", "OPENAI_API_KEY": "host-key",
                                    "DEEPSEEK_API_KEY": "host-key", "PYTHONPATH": "/host/plugins"}):
            env = server.worker_env(self.root)
        for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "PYTHONPATH"):
            self.assertNotIn(key, env)
        self.assertEqual(env["HERMES_HOME"], str(self.root))
        self.assertEqual(env["HERMES_MANAGED_DIR"], str(self.root / "managed-empty"))
        self.assertEqual(env["HERMES_SAFE_MODE"], "1")
        self.assertEqual(env["HERMES_DISABLE_LAZY_INSTALLS"], "1")
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")

    def test_isolated_interpreter_entrypoints_find_sibling_modules(self):
        # Windows ._pth and Python -I omit scripts/ from sys.path. The real
        # entrypoints must work, even when cwd has no project modules.
        help_result = subprocess.run([sys.executable, "-I", str(SCRIPTS / "recruit-server.py"), "--help"],
                                     cwd=self.root, capture_output=True, timeout=10)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        result = subprocess.run([sys.executable, "-I", str(SCRIPTS / "recruit_worker.py")],
                                input=json.dumps({"mode": "extract", "payload": upload("a.txt", "虚构简历".encode())}),
                                cwd=self.root, capture_output=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["text"], "虚构简历")

    def test_worker_home_resolves_inside_request_on_both_platforms(self):
        import ntpath
        import posixpath
        with patch.dict(os.environ, {"HOME": "/host-home", "USERPROFILE": "C:\\host-profile",
                                     "HOMEDRIVE": "C:", "HOMEPATH": "\\host-profile"}):
            env = server.worker_env(self.root)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(ntpath.expanduser("~"), str(self.root))
            self.assertEqual(posixpath.expanduser("~"), str(self.root))
        result = subprocess.run(
            [sys.executable, "-I", "-c", "from pathlib import Path; print(Path.home())"],
            cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), self.root)

    def test_shutdown_terminates_worker_and_cleans_temporary_data(self):
        app = server.RecruitApp(self.root)
        launched = threading.Event()
        launch = subprocess.Popen
        children, errors = [], []
        def sleeping_child(*args, **kwargs):
            child = launch([sys.executable, "-c", "import time;time.sleep(30)"], **kwargs)
            children.append(child)
            launched.set()
            return child
        def request():
            try:
                app.worker({"mode": "extract", "payload": upload("a.txt", b"test")})
            except RecruitError as exc:
                errors.append(exc)
        with patch.object(server.subprocess, "Popen", side_effect=sleeping_child):
            thread = threading.Thread(target=request)
            thread.start()
            self.assertTrue(launched.wait(5))
            app.stop_workers()
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(len(errors), 1)
        self.assertFalse(app.children)
        self.assertEqual(list((app.store.data / "runs").iterdir()), [])
        with self.assertRaisesRegex(RecruitError, "正在停止"):
            app.worker({"mode": "extract", "payload": upload("a.txt", b"test")})


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.job = {"config": json.loads((ASSETS / "config.default.json").read_text()),
                    "api_key": "fixture-key-not-real", "system": "技能", "user": "问题"}
        self.kwargs = None
        self.closed = False

    def agent(self, **kwargs):
        self.kwargs = kwargs
        owner = self
        class FakeAgent:
            tools = []
            valid_tool_names = set()
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda: None)))
            def _create_openai_client(self, *args, **kwargs):
                return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda: None)))
            def run_conversation(self, user, **options):
                owner.assertEqual(options, {"system_message": "技能", "conversation_history": []})
                return {"completed": True, "final_response": "已完成"}
            def close(self):
                owner.closed = True
        return FakeAgent()

    def test_default_adapter_has_no_tools_and_bounded_calls(self):
        self.assertEqual(run_agent_job(self.job, self.agent), {"answer": "已完成"})
        self.assertEqual(self.kwargs["enabled_toolsets"], [])
        self.assertEqual(self.kwargs["max_iterations"], 1)
        self.assertEqual(self.kwargs["max_tokens"], 4096)
        for key in ("skip_context_files", "skip_memory", "skip_background_review"):
            self.assertTrue(self.kwargs[key])
        self.assertEqual(self.kwargs["reasoning_config"], {"enabled": False})
        self.assertIsNone(self.kwargs["fallback_model"])
        self.assertTrue(self.closed)

    def test_allow_tools_is_explicit(self):
        self.job["config"]["allow_tools"] = True
        run_agent_job(self.job, self.agent)
        self.assertEqual(self.kwargs["enabled_toolsets"], ["terminal", "file"])

    def test_schema_drift_fails_closed_and_closes_agent(self):
        def unexpected(**kwargs):
            agent = self.agent(**kwargs)
            agent.tools = [{"function": {"name": "terminal"}}]
            return agent
        with self.assertRaisesRegex(RecruitError, "工具权限"):
            run_agent_job(self.job, unexpected)
        self.assertTrue(self.closed)

    def test_provider_failure_does_not_expose_provider_text(self):
        def failing(**kwargs):
            agent = self.agent(**kwargs)
            agent.run_conversation = lambda *a, **k: {"failed": True, "error": "fixture-secret"}
            return agent
        with self.assertRaises(RecruitError) as exc:
            run_agent_job(self.job, failing)
        self.assertNotIn("fixture-secret", str(exc.exception))
        self.assertTrue(self.closed)


if __name__ == "__main__":
    unittest.main()
