"""Recruit adapter contract against the installed Hermes and real OpenAI SDK.

No credentials or network are needed: only the SDK HTTP transport is replaced.
Run with the packaged venv so this exercises the versions shipped by CI.
"""
import contextlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import recruit_worker
from recruit_core import RecruitError

spec = importlib.util.spec_from_file_location("recruit_server_contract", SCRIPTS / "recruit-server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def completion(message=None, finish="stop"):
    return {
        "id": "recruit-contract-response", "object": "chat.completion", "created": 0,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "message": message or {"role": "assistant", "content": "公司资料：双休。"},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 12, "total_tokens": 112},
    }


class EngineContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("run_agent") is None:
            if os.environ.get("CI"):
                raise RuntimeError("release CI must install Hermes before running the recruitment contract")
            raise unittest.SkipTest("no Hermes installed; release CI must run this with the packaged venv")
        cls.root = tempfile.TemporaryDirectory(prefix="recruit-engine-test-")
        cls.home = Path(cls.root.name)
        (cls.home / "workspace").mkdir()
        (cls.home / "config.yaml").write_text(server.RUNTIME_CONFIG, encoding="utf-8")
        cls.stack = contextlib.ExitStack()
        cls.stack.enter_context(patch.dict(os.environ, server.worker_env(cls.home), clear=True))
        old_cwd = os.getcwd()
        cls.stack.callback(os.chdir, old_cwd)
        os.chdir(cls.home / "workspace")
        # A metadata refresh must not escape to the internet during a contract
        # test. Model replies go through MockTransport and never use sockets.
        cls.stack.enter_context(patch.object(socket.socket, "connect", side_effect=OSError("network disabled in recruit contract test")))
        cls.stack.enter_context(patch.object(socket.socket, "connect_ex", side_effect=OSError("network disabled in recruit contract test")))
        cls.stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=OSError("DNS disabled in recruit contract test")))
        cls.old_logging = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            # Import failures with an installed engine are failures, never skips.
            import httpx
            import openai
            from run_agent import AIAgent
            from agent import models_dev
        cls.httpx, cls.openai, cls.agent_class = httpx, openai, AIAgent
        cls.stack.enter_context(patch.object(models_dev, "fetch_models_dev", return_value={}))

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()
        logging.disable(cls.old_logging)
        cls.root.cleanup()

    def setUp(self):
        self.requests = []
        self.clients = []
        self.agents = []
        self.reply = completion()
        self.status = 200
        self.job = {
            "mode": "agent", "api_key": "recruit-contract-dummy-key",
            "config": {"model": "deepseek-flash", "allow_tools": False, "max_tokens": 512},
            "system": "只按公司资料回答。公司资料：双休。", "user": "上班时间是什么？",
        }

    def transport(self, request):
        self.requests.append(json.loads(request.content))
        if len(self.requests) > 4:
            raise AssertionError("unexpected unbounded model loop")
        self.assertEqual(request.url.host, "api.deepseek.com")
        self.assertEqual(request.url.path, "/v1/chat/completions")
        return self.httpx.Response(self.status, json=self.reply)

    def client_factory(self, agent, kwargs, *, reason, shared):
        options = dict(kwargs)
        options["http_client"] = self.httpx.Client(transport=self.httpx.MockTransport(self.transport))
        client = self.openai.OpenAI(**options)
        self.clients.append(client)
        return client

    def agent_factory(self, **kwargs):
        agent = self.agent_class(**kwargs)
        self.agents.append(agent)
        original = agent.close
        agent.close = Mock(wraps=original)
        return agent

    @contextlib.contextmanager
    def isolated_call(self):
        # The no-tool path must not launch even an environment probe. Also
        # reject model-solicited dispatch independently of whether a command
        # actually changes anything on the host.
        with contextlib.ExitStack() as stack:
            def client_factory(agent, kwargs, *, reason, shared):
                return self.client_factory(agent, kwargs, reason=reason, shared=shared)
            stack.enter_context(patch.object(self.agent_class, "_create_openai_client", new=client_factory))
            # Python's platform.processor() uses `uname -p` on some Linux
            # builds. Keep this SDK telemetry lookup deterministic while the
            # command guard below still catches lazy installs/tool execution.
            stack.enter_context(patch("platform.processor", return_value="contract-cpu"))
            stack.enter_context(patch("platform._Processor.get", return_value="contract-cpu"))
            launches = stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("recruit must not spawn commands")))
            dispatch = stack.enter_context(patch("model_tools.handle_function_call", side_effect=AssertionError("recruit must not dispatch a tool")))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            yield launches, dispatch
            launches.assert_not_called()
            dispatch.assert_not_called()

    def assert_closed(self):
        self.assertTrue(self.agents)
        for agent in self.agents:
            agent.close.assert_called_once()
        self.assertTrue(self.clients)
        self.assertTrue(all(client.max_retries == 0 for client in self.clients))
        self.assertTrue(all(client.is_closed() for client in self.clients))

    def test_real_agent_sends_one_bounded_no_tool_request(self):
        with self.isolated_call():
            result = recruit_worker.run_agent_job(self.job, agent_class=self.agent_factory)
        self.assertEqual(result["answer"], "公司资料：双休。")
        self.assertEqual(len(self.requests), 1)
        body = self.requests[0]
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["max_tokens"], 512)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertFalse(body.get("tools"))
        self.assertFalse(body.get("stream"))
        self.assertIn(self.job["system"], json.dumps(body["messages"], ensure_ascii=False))
        agent = self.agents[0]
        self.assertEqual(agent.valid_tool_names, set())
        self.assertFalse(agent._environment_probe)
        self.assertEqual(agent._api_max_retries, 1)
        self.assertEqual(agent._auto_recovery_cycles, 0)
        self.assertTrue(agent.skip_background_review)
        self.assertIsNone(agent._memory_manager)
        self.assertFalse(agent.compression_enabled)
        self.assert_closed()

    def test_provider_failure_is_chinese_and_never_retried(self):
        self.status = 401
        self.reply = {"error": {"message": "private provider detail recruit-contract-dummy-key", "type": "authentication_error"}}
        with self.isolated_call(), self.assertRaises(RecruitError) as caught:
            recruit_worker.run_agent_job(self.job, agent_class=self.agent_factory)
        self.assertIn("模型未完成回答", str(caught.exception))
        self.assertNotIn("private provider", str(caught.exception))
        self.assertNotIn(self.job["api_key"], str(caught.exception))
        self.assertEqual(len(self.requests), 1)
        self.assert_closed()

    def test_rate_limit_does_not_trigger_sdk_or_agent_retries(self):
        self.status = 429
        self.reply = {"error": {"message": "contract rate limit", "type": "rate_limit_error"}}
        with self.isolated_call(), self.assertRaises(RecruitError):
            recruit_worker.run_agent_job(self.job, agent_class=self.agent_factory)
        self.assertEqual(len(self.requests), 1)
        self.assert_closed()

    def test_server_error_does_not_trigger_recovery_calls(self):
        self.status = 500
        self.reply = {"error": {"message": "contract server error", "type": "server_error"}}
        with self.isolated_call(), self.assertRaises(RecruitError):
            recruit_worker.run_agent_job(self.job, agent_class=self.agent_factory)
        self.assertEqual(len(self.requests), 1)
        self.assert_closed()

    def test_hallucinated_terminal_call_cannot_execute(self):
        self.reply = completion({"role": "assistant", "content": None, "tool_calls": [{
            "id": "forbidden-call", "type": "function",
            "function": {"name": "terminal", "arguments": '{"command":"echo forbidden"}'},
        }]}, "tool_calls")
        with self.isolated_call(), self.assertRaises(RecruitError):
            recruit_worker.run_agent_job(self.job, agent_class=self.agent_factory)
        self.assertEqual(len(self.requests), 1, "iteration-limit summaries must not create extra paid calls")
        self.assert_closed()

    def test_opt_in_exposes_only_terminal_and_file_schemas(self):
        self.job["config"]["allow_tools"] = True
        # Construction only: tool opt-in is deliberately unsandboxed and is
        # never exercised with model-generated tool calls in this test.
        def factory(**kwargs):
            agent = self.agent_factory(**kwargs)
            self.assertTrue(agent.valid_tool_names)
            self.assertLessEqual(agent.valid_tool_names,
                                 {"terminal", "process_manage", "read_file", "write_file", "patch", "search_files"})
            agent.run_conversation = Mock(return_value={"completed": True, "final_response": "schema inspection only"})
            return agent
        with self.isolated_call():
            recruit_worker.run_agent_job(self.job, agent_class=factory)
        self.assertEqual(self.requests, [])
        self.assert_closed()


if __name__ == "__main__":
    unittest.main(verbosity=2)
