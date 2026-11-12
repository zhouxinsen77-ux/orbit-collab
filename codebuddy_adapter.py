"""WorkBuddy official CodeBuddy CLI adapter. No token is stored by this module."""
from pathlib import Path
import argparse
import json
import os
import signal
import subprocess
import threading
import time
import tempfile

from config import ROOT, DATA_DIR, NODE, CODEBUDDY_ENTRY
CLI = CODEBUDDY_ENTRY
CONFIG = Path(os.environ.get('CODEBUDDY_CONFIG_DIR', str(Path.home() / '.codebuddy'))).expanduser().resolve()
VERIFIED = DATA_DIR / 'workbuddy-verified.json'

def detect_provider():
    authenticated = False
    try:
        authenticated = json.loads(VERIFIED.read_text()).get('ok') is True
    except (OSError, ValueError):
        pass
    return {'id': 'workbuddy', 'name': 'WorkBuddy / CodeBuddy', 'kind': 'cli',
            'available': CLI.is_file() and authenticated, 'installed': CLI.is_file(),
            'authenticated': authenticated, 'version': '2.181.0',
            'detail': ('官方 CLI 已验证；可规划、写作和审查。此接入关闭系统工具，代码执行交给 Codex 或 Harness。' if authenticated else
                       '请安装官方 CLI，在终端运行 python3 codebuddy_adapter.py login，然后执行 verify 验证。'),
            'setup_action': 'login_workbuddy',
            'documentation_url': 'https://www.workbuddy.ai/docs/cli/headless',
            'config_source': str(CONFIG)}

def environment():
    env = os.environ.copy()
    env.update({'CODEBUDDY_CONFIG_DIR': str(CONFIG), 'DISABLE_AUTOUPDATER': '1',
                'CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS': '1',
                'PATH': str(NODE.parent) + os.pathsep + env.get('PATH', '')})
    return env

def run(prompt, schema, workdir, cancel_event, timeout=300):
    if not CLI.is_file():
        raise RuntimeError('WorkBuddy 官方 CLI 尚未安装')
    folder = Path(workdir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if 'tasks' in schema.get('properties', {}):
        role_instruction = ('你只负责规划，不执行用户目标。最终工具参数必须直接是'
            '{"summary":"计划摘要","tasks":[{"id":"node1","title":"节点标题","role":"executor",'
            '"instructions":"可执行要求与验收标准","depends_on":[]}]}。tasks必须是数组。')
    elif 'verdict' in schema.get('properties', {}):
        role_instruction = ('你只负责独立审查，不执行整项任务。对照提供的产物与检查，真实判断pass或revise。'
            '最终工具参数必须直接是{"verdict":"pass或revise","summary":"审查理由","findings":[]}。findings必须是字符串数组。')
    else:
        role_instruction = '你只完成分配给你的正文节点，不能执行系统工具，不能编造文件或命令检查。'
    system_prompt = ('你是本机工作流中的一个独立节点。遵守用户给出的授权边界。只允许调用StructuredOutput提交最终结果，'
        '其参数直接为目标JSON对象，严禁使用data、output、result等额外包装层。不要把数组编码为字符串。'
        + role_instruction)
    command = [str(CLI), '-p', '--output-format', 'json', '--json-schema', json.dumps(schema),
               '--tools', 'StructuredOutput', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
               '--no-session-persistence', '--max-turns', '3', '--setting-sources', '', '--system-prompt', system_prompt]
    started = time.monotonic()
    prompt_file = tempfile.TemporaryFile(mode='w+t', encoding='utf-8')
    prompt_file.write(str(prompt) + '\n使用 StructuredOutput 提交符合指定 schema 的结果。所有必填字段必须提供，'
                      '不要增加 data 包装层，数组字段必须为数组。没有文件、终端或网络工具，不得声称执行了文件或命令操作。')
    prompt_file.seek(0)
    stdout_file = tempfile.TemporaryFile(mode='w+b')
    stderr_file = tempfile.TemporaryFile(mode='w+b')
    proc = subprocess.Popen(command, cwd=folder, env=environment(), stdin=prompt_file,
                            stdout=stdout_file, stderr=stderr_file, start_new_session=True)
    from server import register_child, clear_child
    child_record = register_child(proc, folder)
    try:
        while proc.poll() is None:
            if cancel_event.is_set():
                raise RuntimeError('WorkBuddy 任务已取消')
            if time.monotonic() - started > timeout:
                raise RuntimeError('WorkBuddy 执行超时')
            cancel_event.wait(.2)
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout_bytes, stderr_bytes = stdout_file.read(), stderr_file.read()
        (folder / '_transport.json').write_text(json.dumps({'exit_code': proc.returncode,
            'stdout_bytes': len(stdout_bytes), 'stderr_bytes': len(stderr_bytes), 'transport': 'file-descriptors'}))
        stdout, stderr = stdout_bytes.decode('utf-8'), stderr_bytes.decode('utf-8', errors='replace')
        if 'Authentication required' in stderr or 'Please use /login' in stderr:
            VERIFIED.unlink(missing_ok=True)
            raise RuntimeError('WorkBuddy 尚未登录，请在终端运行 python3 codebuddy_adapter.py login 完成本人认证')
        if proc.returncode:
            raise RuntimeError('WorkBuddy 执行失败，退出码 ' + str(proc.returncode))
        try:
            result = json.loads(stdout)
            if isinstance(result, list):
                result = next((item for item in reversed(result) if isinstance(item, dict) and item.get('type') == 'result'), {})
            (folder / '_provider-result.json').write_text(json.dumps({k: result.get(k) for k in
                ('subtype', 'is_error', 'result', 'structured_output', 'num_turns')}, ensure_ascii=False), encoding='utf-8')
            if result.get('is_error'):
                raise ValueError('provider error')
            value = result.get('structured_output')
            if value is None:
                value = result.get('result', result)
            if isinstance(value, str):
                value = json.loads(value)
            if not isinstance(value, dict):
                raise ValueError('not an object')
            (folder / '_response.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            return value
        except (ValueError, AttributeError) as error:
            raise RuntimeError('WorkBuddy 未返回可验收的结构化结果；请检查本人登录和模型服务') from error
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=3)
            except ProcessLookupError:
                pass
        clear_child(child_record)
        prompt_file.close()
        stdout_file.close()
        stderr_file.close()

def verify():
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False}
    result = run('连接验证，请返回 ok=true；不要使用任何工具。', schema, DATA_DIR / 'provider-checks/workbuddy', threading.Event(), 90)
    if result.get('ok') is not True:
        raise RuntimeError('WorkBuddy 连接验证未通过')
    VERIFIED.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    VERIFIED.write_text(json.dumps({'ok': True, 'verified_at': time.time(), 'version': '2.181.0'}))
    print('WorkBuddy 已验证，可以在平台中分配任务。')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['login', 'verify', 'status'])
    action = parser.parse_args().action
    try:
        if action == 'login':
            if not CLI.is_file():
                raise RuntimeError('请先安装 WorkBuddy / CodeBuddy CLI，见 README')
            raise SystemExit(subprocess.call([str(CLI)], env=environment()))
        elif action == 'verify':
            verify()
        else:
            print(json.dumps(detect_provider(), ensure_ascii=False, indent=2))
    except Exception as error:
        print(str(error))
        raise SystemExit(1)
