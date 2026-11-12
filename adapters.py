"""Installed agent providers; credentials are read from their existing source."""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import re
import signal
import subprocess
import time
import tempfile

from config import ROOT, NODE, DSH, DEEPSEEK_ENV
HOME = Path.home()
HARNESS_VERSION = "0.2.2-rc.1"


def _read_env(path: Path) -> dict[str, str]:
    """Parse a dotenv source as data, without shell expansion or execution."""
    result = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return result
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        result[key] = value
    return result


def _harness_credentials() -> dict[str, str]:
    source = _read_env(DEEPSEEK_ENV)
    allowed = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL")
    return {key: os.environ.get(key) or source.get(key, "") for key in allowed}


def _app_metadata(path: str) -> dict:
    app = Path(path)
    data = {"installed": app.is_dir(), "app_path": str(app), "version": None}
    try:
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        data["version"] = info.get("CFBundleShortVersionString")
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    return data


def detect_providers() -> list[dict]:
    credentials = _harness_credentials()
    installed = DSH.is_file() and NODE.is_file()
    configured = bool(credentials.get("DEEPSEEK_API_KEY"))
    grok = _app_metadata("/Applications/Grok Bot.app")
    doubao = _app_metadata("/Applications/Doubao.app")
    grok_login = None
    try:
        status = json.loads((HOME / "Library/Application Support/Grok Bot/desktop-status.json").read_text())
        grok_login = status.get("signedIn") if isinstance(status.get("signedIn"), bool) else None
    except (OSError, ValueError):
        pass
    result = [
        {
            "id": "deepseek_harness", "name": "DeepSeek Harness", "kind": "cli",
            "available": installed and configured, "authenticated": configured,
            "version": HARNESS_VERSION if installed else None,
            "detail": ("官方 dsh headless 已安装；凭据读取用户配置的环境变量或 .env，运行结果按任务单独验收。"
                       if installed and configured else "缺少官方 dsh 运行时或 DEEPSEEK_API_KEY；不会以普通 API 冒充 Harness。"),
            "credential_source": str(DEEPSEEK_ENV),
            "credential_key": "DEEPSEEK_API_KEY",
            "documentation_url": "https://github.com/deepseek-ai/deepseek-harness",
        },
        {
            "id": "grok", "name": "Grok Bot", "kind": "manual",
            "available": True, "authenticated": grok_login is True,
            "detail": "桌面交接：复制任务到 Grok Bot，再回填结果；不自动操作客户端或既有任务。",
            "handoff_url": "grokbot://" if grok["installed"] else "https://grok.com/", "login_verified": grok_login,
            **grok,
        },
        {
            "id": "doubao", "name": "豆包", "kind": "manual",
            "available": True, "authenticated": False,
            "detail": "人工交接可用；登录状态未核验。复制任务到豆包，再回填结果；没有声称自动接通豆包 API。",
            "handoff_url": "doubao://" if doubao["installed"] else "https://www.doubao.com/", "login_verified": None,
            **doubao,
        },
    ]
    try:
        import codebuddy_adapter
    except ImportError:
        result.append({"id": "workbuddy", "name": "WorkBuddy / CodeBuddy", "kind": "cli",
                       "available": False, "authenticated": False,
                       "detail": "官方 CodeBuddy CLI 接入尚未完成。"})
    else:
        result.append(codebuddy_adapter.detect_provider())
    return result


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _parse_json_answer(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
        if match:
            text = match.group(1).strip()
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as error:
        raise RuntimeError("Agent 未返回完整 JSON 对象，任务保持失败状态，请重试或回填有效结果。") from error
    if not isinstance(data, dict):
        raise RuntimeError("Agent 返回值必须是 JSON 对象。")
    return data


def run_api(provider_id, prompt, schema, workdir, cancel_event, timeout=300) -> dict:
    """Execute the official CLI. Manual providers never masquerade as execution."""
    if provider_id in {"workbuddy", "codebuddy"}:
        import codebuddy_adapter
        return codebuddy_adapter.run(prompt, schema, workdir, cancel_event, timeout)
    if provider_id != "deepseek_harness":
        raise RuntimeError("该提供方使用桌面交接：请复制任务并回填实际结果。")
    if not DSH.is_file() or not NODE.is_file():
        raise RuntimeError("DeepSeek Harness 官方运行时未安装。")
    credentials = _harness_credentials()
    if not credentials.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("缺少 DEEPSEEK_API_KEY；请在环境变量或配置的 .env 文件中设置，切勿提交到版本库。")
    if cancel_event.is_set():
        raise RuntimeError("任务已取消。")
    workspace = Path(workdir).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    harness_home = workspace / ".harness-home"
    harness_home.mkdir(mode=0o700, exist_ok=True)
    # A separate home prevents concurrent tasks from sharing sessions or settings.
    overlay = harness_home / "platform.patch.yml"
    model = os.environ.get("DEEPSEEK_HARNESS_MODEL") or credentials.get("DEEPSEEK_MODEL") or "deepseek-flash"
    overlay.write_text(
        "- id: session-log-deepseek\n  config:\n    enabled: false\n"
        "- id: session-title-llm\n  disabled: true\n"
        "- id: agent-default-model\n  config:\n    provider: deepseek-official\n    model: "
        + json.dumps(model) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({key: value for key, value in credentials.items() if value})
    env.update({"DSH_HOME": str(harness_home), "DSH_TELEMETRY_MODE": "DISABLED",
                "DSH_TELEMETRY_DISABLED": "1", "NO_COLOR": "1"})
    env["PATH"] = str(NODE.parent) + os.pathsep + env.get("PATH", "")
    task = (str(prompt) + "\n\n只输出一个 JSON 对象，不加 Markdown 围栏，不输出其他说明。"
            "返回 JSON 必须符合以下 schema：\n" + json.dumps(schema, ensure_ascii=False))
    command = [str(NODE), str(DSH), "headless", "--patch", str(overlay),
               "--permission-mode", "workspace-write", "--approval-policy", "never", "-"]
    prompt_file = tempfile.TemporaryFile(mode='w+t', encoding='utf-8')
    prompt_file.write(task)
    prompt_file.seek(0)
    stdout_file = tempfile.TemporaryFile(mode='w+b')
    stderr_file = tempfile.TemporaryFile(mode='w+b')
    process = subprocess.Popen(command, cwd=str(workspace), env=env, stdin=prompt_file,
                               stdout=stdout_file, stderr=stderr_file,
                               start_new_session=True)
    from server import register_child, clear_child
    child_record = register_child(process, workspace)
    started = time.monotonic()
    try:
        while process.poll() is None:
            if cancel_event.is_set():
                raise RuntimeError("任务已取消。")
            if time.monotonic() - started > float(timeout):
                raise RuntimeError("DeepSeek Harness 运行超时。")
            cancel_event.wait(0.2)
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout_bytes, stderr_bytes = stdout_file.read(), stderr_file.read()
        (workspace / '_transport.json').write_text(json.dumps({'exit_code': process.returncode,
            'stdout_bytes': len(stdout_bytes), 'stderr_bytes': len(stderr_bytes), 'transport': 'file-descriptors'}))
        stdout, stderr = stdout_bytes.decode('utf-8'), stderr_bytes.decode('utf-8', errors='replace')
        if process.returncode != 0:
            # Never expose provider reasoning or credential-bearing diagnostics.
            safe_lines = [line for line in stderr.splitlines() if re.search(r"\b(Error|error|failed|Unauthorized|401|402|403|429)\b", line)]
            detail = " ".join(safe_lines[-2:])[:600]
            for value in credentials.values():
                if value:
                    detail = detail.replace(value, "[redacted]")
            detail = re.sub(r"(?:sk-|Bearer\s+)[A-Za-z0-9_.-]+", "[redacted]", detail)
            raise RuntimeError(f"DeepSeek Harness 执行失败（退出码 {process.returncode}）。" + detail)
        return _parse_json_answer(stdout)
    finally:
        _terminate(process)
        clear_child(child_record)
        prompt_file.close()
        stdout_file.close()
        stderr_file.close()
