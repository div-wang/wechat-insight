import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "windows_agent.py"


def load_module():
    spec = importlib.util.spec_from_file_location("windows_agent", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status


class WindowsAgentTests(unittest.TestCase):
    def test_load_export_records_falls_back_to_export_metadata(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            data_dir = pathlib.Path(td)
            actual = data_dir / "messages_20260722_20260724.json"
            actual.write_text('[{"timestamp": 1}]', encoding="utf-8")
            (data_dir / "export_meta.json").write_text(
                json.dumps({"json_file": actual.name}), encoding="utf-8"
            )

            records, path = module.load_export_records(
                {"data_dir": td, "poll_lookback_days": 2}, initial_sync=False
            )

            self.assertEqual(records, [{"timestamp": 1}])
            self.assertEqual(path, actual)

    def test_keys_exist_requires_nonempty_32_byte_key(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "keys.json"
            path.write_text("{}", encoding="utf-8")
            self.assertFalse(module.keys_exist(path))
            path.write_text(json.dumps({"message_0": "ab" * 32}), encoding="utf-8")
            self.assertTrue(module.keys_exist(path))

    def test_post_messages_builds_lan_payload_and_bearer_token(self):
        module = load_module()
        captured = {}

        def urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

        result = module.post_messages(
            "http://192.168.1.8:8080/messages",
            [{"chat_id": "a", "timestamp": 1, "content": "hello"}],
            token="secret",
            timeout=7,
            urlopen=urlopen,
        )

        self.assertTrue(result)
        self.assertEqual(captured["timeout"], 7)
        self.assertEqual(
            captured["request"].headers["Authorization"], "Bearer secret"
        )
        payload = json.loads(captured["request"].data.decode("utf-8"))
        self.assertEqual(payload["schema_version"], "wechat-insight.lan.v1")
        self.assertEqual(payload["message_count"], 1)

    def test_report_pending_only_sends_unreported_messages(self):
        module = load_module()
        records = [
            {"chat_id": "a", "timestamp": 1, "msg_type": 1, "content": "one"},
            {"chat_id": "a", "timestamp": 2, "msg_type": 1, "content": "two"},
        ]
        state = {"reported_ids": [module.message_id(records[0])]}
        calls = []
        original = module.post_messages
        module.post_messages = lambda _url, messages, **_kwargs: calls.append(messages) or True
        try:
            sent = module.report_pending(
                records,
                {"lan_report_url": "http://server/messages", "lan_report_batch_size": 10},
                state,
            )
        finally:
            module.post_messages = original

        self.assertEqual(sent, 1)
        self.assertEqual(calls, [[records[1]]])
        self.assertEqual(len(state["reported_ids"]), 2)

    def test_report_pending_persists_each_successful_batch(self):
        module = load_module()
        records = [
            {"chat_id": "a", "timestamp": index, "msg_type": 1, "content": str(index)}
            for index in range(3)
        ]
        state = {}
        snapshots = []
        original = module.post_messages
        module.post_messages = lambda *_args, **_kwargs: True
        try:
            sent = module.report_pending(
                records,
                {"lan_report_url": "http://server/messages", "lan_report_batch_size": 1},
                state,
                on_progress=lambda current: snapshots.append(
                    list(current["reported_ids"])
                ),
            )
        finally:
            module.post_messages = original

        self.assertEqual(sent, 3)
        self.assertEqual([len(snapshot) for snapshot in snapshots], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
