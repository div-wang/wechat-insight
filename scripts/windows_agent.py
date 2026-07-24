#!/usr/bin/env python3
"""Hidden Windows collector for scheduled, incremental WeChat exports.

The agent deliberately keeps LAN reporting separate from the local analysis
pipeline. Reporting is disabled until ``lan_report_url`` is explicitly set in
the normal wechat-insight configuration file.
"""

import argparse
import hashlib
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone


APP_NAME = "wechat-insight"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
STATE_ROOT = pathlib.Path(
    os.environ.get("LOCALAPPDATA") or pathlib.Path.home()
) / APP_NAME
DEFAULT_CONFIG_PATH = STATE_ROOT / "wechat-insight.json"
DEFAULT_KEYS_PATH = STATE_ROOT / "wechat-keys.json"
DEFAULT_AGENT_STATE_PATH = STATE_ROOT / "agent-state.json"
DEFAULT_LOG_PATH = STATE_ROOT / "logs" / "agent.log"
DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_LOOKBACK_DAYS = 2
DEFAULT_BATCH_SIZE = 200
MAX_REPORTED_IDS = 100000


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {} if default is None else default


def write_json_atomic(path, payload):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp_path, path)


def append_log(message, log_path=DEFAULT_LOG_PATH):
    log_path = pathlib.Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


def keys_exist(keys_path=DEFAULT_KEYS_PATH):
    payload = load_json(keys_path, {})
    return isinstance(payload, dict) and any(
        isinstance(value, str) and len(value) >= 64 for value in payload.values()
    )


def message_id(message):
    values = (
        message.get("chat_id"),
        message.get("timestamp"),
        message.get("real_sender_id"),
        message.get("msg_type"),
        message.get("content"),
    )
    raw = json.dumps(
        values, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def executable_candidates():
    here = pathlib.Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    return [
        here.parent.parent / "cli" / "wechat-insight.exe",
        here.parent / "wechat-insight.exe",
        repo_root / "wechat-insight.cmd",
        repo_root / "wechat_insight_cli.py",
    ]


def resolve_cli_command(args):
    for candidate in executable_candidates():
        if not candidate.exists():
            continue
        suffix = candidate.suffix.lower()
        if suffix == ".cmd":
            return ["cmd.exe", "/d", "/c", str(candidate), *args]
        if suffix == ".py":
            return [sys.executable, str(candidate), *args]
        return [str(candidate), *args]
    raise FileNotFoundError("未找到 wechat-insight 可执行文件")


def run_cli(args, timeout=None):
    command = resolve_cli_command(args)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if result.stdout.strip():
        append_log(result.stdout.strip())
    if result.stderr.strip():
        append_log("stderr: " + result.stderr.strip())
    return result.returncode


def load_export_records(config, initial_sync):
    data_dir = pathlib.Path(config.get("data_dir") or (STATE_ROOT / "data"))
    filename = "messages_all.json" if initial_sync else (
        f"messages_last{int(config.get('poll_lookback_days', DEFAULT_LOOKBACK_DAYS))}d.json"
    )
    path = data_dir / filename
    if not path.exists():
        metadata = load_json(data_dir / "export_meta.json", {})
        metadata_name = metadata.get("json_file")
        if metadata_name:
            path = data_dir / metadata_name
    payload = load_json(path, [])
    if not isinstance(payload, list):
        raise ValueError(f"导出文件不是 JSON 数组: {path}")
    return payload, path


def build_report_payload(messages):
    return {
        "schema_version": "wechat-insight.lan.v1",
        "device_name": socket.gethostname(),
        "generated_at": utc_now(),
        "message_count": len(messages),
        "messages": messages,
    }


def post_messages(url, messages, token="", timeout=10, urlopen=None):
    if not url:
        return False
    body = json.dumps(
        build_report_payload(messages), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "wechat-insight-agent/1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urlopen or urllib.request.urlopen
    with opener(request, timeout=timeout) as response:
        status = getattr(response, "status", response.getcode())
        if not 200 <= int(status) < 300:
            raise RuntimeError(f"上报接口返回 HTTP {status}")
    return True


def report_pending(records, config, state, on_progress=None):
    url = str(config.get("lan_report_url") or "").strip()
    if not url:
        return 0

    reported_ids = list(state.get("reported_ids") or [])
    reported_set = set(reported_ids)
    pending = [
        (message_id(record), record)
        for record in records
        if message_id(record) not in reported_set
    ]
    batch_size = max(1, int(config.get("lan_report_batch_size", DEFAULT_BATCH_SIZE)))
    timeout = max(1, int(config.get("lan_report_timeout_seconds", 10)))
    token = str(config.get("lan_report_token") or "")
    sent = 0

    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        post_messages(
            url,
            [record for _identity, record in batch],
            token=token,
            timeout=timeout,
        )
        reported_ids.extend(identity for identity, _record in batch)
        reported_ids = reported_ids[-MAX_REPORTED_IDS:]
        state["reported_ids"] = reported_ids
        state["last_report_at"] = utc_now()
        if on_progress:
            on_progress(state)
        sent += len(batch)
    return sent


def run_cycle(
    config_path=DEFAULT_CONFIG_PATH,
    keys_path=DEFAULT_KEYS_PATH,
    state_path=DEFAULT_AGENT_STATE_PATH,
):
    config_path = pathlib.Path(config_path)
    keys_path = pathlib.Path(keys_path)
    state_path = pathlib.Path(state_path)

    if not keys_exist(keys_path):
        append_log("未检测到有效 key，开始执行 setup")
        if run_cli(["setup"], timeout=240) != 0 or not keys_exist(keys_path):
            raise RuntimeError("setup 未能获取有效 key")
        append_log("setup 已获取 key")

    config = load_json(config_path, {})
    state = load_json(state_path, {})
    reporting_enabled = bool(str(config.get("lan_report_url") or "").strip())
    initial_sync = (
        not bool(state.get("initial_export_complete"))
        or (reporting_enabled and not bool(state.get("initial_report_complete")))
    )
    export_args = ["export"]
    if not initial_sync:
        lookback = max(1, int(config.get("poll_lookback_days", DEFAULT_LOOKBACK_DAYS)))
        export_args.extend(["--days", str(lookback)])
    if run_cli(export_args, timeout=240) != 0:
        raise RuntimeError("聊天记录导出失败")

    records, export_path = load_export_records(config, initial_sync)
    sent = report_pending(
        records,
        config,
        state,
        on_progress=lambda current: write_json_atomic(state_path, current),
    )
    state.update({
        "initial_export_complete": True,
        "initial_report_complete": (
            True if reporting_enabled else bool(state.get("initial_report_complete"))
        ),
        "last_cycle_at": utc_now(),
        "last_export_file": str(export_path),
        "last_export_count": len(records),
        "last_report_count": sent,
        "last_error": None,
    })
    write_json_atomic(state_path, state)
    append_log(f"采集完成: {len(records)} 条，上报: {sent} 条")
    return {"exported": len(records), "reported": sent, "path": str(export_path)}


def acquire_single_instance():
    if os.name != "nt":
        return object()
    import ctypes

    handle = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Local\\WeChatInsightCollector"
    )
    if not handle or ctypes.windll.kernel32.GetLastError() == 183:
        return None
    return handle


def run_forever(config_path, keys_path, state_path, once=False):
    handle = acquire_single_instance()
    if handle is None:
        append_log("已有采集进程正在运行，本进程退出")
        return 0

    while True:
        try:
            result = run_cycle(config_path, keys_path, state_path)
            append_log(f"本轮状态: {result}")
        except Exception as exc:
            state = load_json(state_path, {})
            state.update({"last_cycle_at": utc_now(), "last_error": str(exc)})
            write_json_atomic(state_path, state)
            append_log(f"采集失败: {exc}\n{traceback.format_exc()}")
        if once:
            return 0
        config = load_json(config_path, {})
        interval = max(
            15, int(config.get("poll_interval_seconds", DEFAULT_INTERVAL_SECONDS))
        )
        time.sleep(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="WeChat Insight Windows 后台采集器")
    parser.add_argument("--once", action="store_true", help="只执行一轮")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--keys", default=str(DEFAULT_KEYS_PATH))
    parser.add_argument("--state", default=str(DEFAULT_AGENT_STATE_PATH))
    args = parser.parse_args(argv)
    return run_forever(args.config, args.keys, args.state, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
