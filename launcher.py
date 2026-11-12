#!/usr/bin/env python3
"""Start only this Orbit instance; never adopt another service on the same port."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

from config import ROOT, DATA_DIR, NODE, PORT, INSTANCE_ID

STATE = DATA_DIR
URL = f'http://127.0.0.1:{PORT}'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health():
    try:
        with OPENER.open(URL + '/api/health', timeout=2) as response:
            result = json.load(response)
        if (isinstance(result, dict) and result.get('local_only') is True
                and result.get('instance_id') == INSTANCE_ID):
            return result
    except (OSError, ValueError):
        pass
    return None


def occupied():
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=.5):
            return True
    except OSError:
        return False


def process_record(pid):
    command = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'command='], text=True).strip()
    started = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'lstart='], text=True).strip()
    return {'pid': pid, 'command': command, 'started': started, 'instance_id': INSTANCE_ID}


def stop_owned(pidfile):
    if not pidfile.is_file():
        print('没有此启动器登记的进程。')
        return
    try:
        record = json.loads(pidfile.read_text())
        if not isinstance(record, dict) or record.get('instance_id') != INSTANCE_ID:
            raise RuntimeError('进程登记不属于此 Orbit 实例，未停止任何程序。')
        pid = record.get('pid')
        if not isinstance(pid, int) or pid <= 1:
            raise RuntimeError('进程登记无效，未停止任何程序。')
        try:
            actual = process_record(pid)
        except subprocess.CalledProcessError:
            pidfile.unlink(missing_ok=True)
            print('已登记的 Orbit 进程已结束。')
            return
        if (str(ROOT / 'server.py') not in actual['command']
                or actual['started'] != record.get('started')
                or actual['command'] != record.get('command')):
            raise RuntimeError('进程身份已变化，未停止其他程序。')
        import autoloop_service
        autoloop_service.stop()
        os.kill(pid, signal.SIGTERM)
        for _ in range(40):
            if not health():
                break
            time.sleep(.1)
        pidfile.unlink(missing_ok=True)
        print('Orbit 已停止；任务记录仍保存在配置的数据目录。')
    except (OSError, ValueError) as error:
        raise RuntimeError('无法读取此实例的进程登记；未尝试停止未知程序。') from error


def main():
    parser = argparse.ArgumentParser(description='轨道协作 Orbit 本机启动器')
    parser.add_argument('action', choices=['start', 'status', 'stop'], nargs='?', default='start')
    parser.add_argument('--open', action='store_true')
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    pidfile = STATE / 'server-process.json'
    current = health()
    if args.action == 'status':
        print(json.dumps({'url': URL, 'running': bool(current), 'health': current,
                          'port_in_use_by_other_instance': not current and occupied()}, ensure_ascii=False, indent=2))
        return
    if args.action == 'stop':
        stop_owned(pidfile)
        return
    if not current:
        if occupied():
            raise RuntimeError(f'端口 {PORT} 已被其他服务占用；请在 orbit.json 设置其他 port（如 8876）。')
        env = os.environ.copy()
        if NODE.is_file():
            env['PATH'] = str(NODE.parent) + os.pathsep + env.get('PATH', '')
        with open(STATE / 'server.log', 'ab', buffering=0) as log:
            proc = subprocess.Popen([sys.executable, '-u', str(ROOT / 'server.py'), '--port', str(PORT)],
                                    cwd=ROOT, env=env, stdout=log, stderr=log,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(80):
            if proc.poll() is not None:
                raise RuntimeError('服务启动失败，请查看配置数据目录中的 server.log。')
            if health():
                pidfile.write_text(json.dumps(process_record(proc.pid)))
                pidfile.chmod(0o600)
                break
            time.sleep(.25)
        else:
            # Terminate only the process created above, never a port's unknown owner.
            proc.terminate()
            raise RuntimeError('此 Orbit 实例尚未就绪；已停止本次启动的进程，请检查 server.log。')
    print('轨道协作 Orbit：' + URL)
    if (STATE / 'autoloop/config.json').is_file():
        import autoloop_service
        try:
            autoloop_service.start()
            print('可选 Autoloop 事件入口已就绪。')
        except RuntimeError as error:
            print(str(error))
    if args.open:
        webbrowser.open(URL)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
