"""Run one local application during its configured daytime window."""
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import night_status

TZ = ZoneInfo('Asia/Shanghai')
STOP = threading.Event()


def is_active(now):
    return 6 <= now.astimezone(TZ).hour < 24


def arguments(environment):
    args = [sys.executable, 'app.py', '--data-dir', '/data', '--bind',
            environment.get('NOTE4_BIND', '127.0.0.1'), '--port',
            environment.get('NOTE4_PORT', '18773')]
    if environment.get('NOTE4_PAGE'):
        args.extend(['--page', environment['NOTE4_PAGE']])
    if environment.get('NOTE4_AUTOCONNECT', '0') == '1':
        args.append('--connect')
    return args


def main():
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    child = None
    night = None
    bind = os.environ.get('NOTE4_BIND', '127.0.0.1')
    port = int(os.environ.get('NOTE4_PORT', '18773'))
    try:
        while not STOP.is_set():
            active = is_active(datetime.now(TZ))
            if active and child is None:
                if night is not None:
                    night_status.stop(night)
                    night = None
                child = subprocess.Popen(arguments(os.environ))
                print('NOTE4 daytime service started', flush=True)
            elif not active and child is not None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                child = None
                print('NOTE4 night pause started', flush=True)
            if not active and night is None:
                night = night_status.start(bind, port)
                print('NOTE4 night status available', flush=True)
            if child is not None and child.poll() is not None:
                print('NOTE4 service exited unexpectedly', flush=True)
                return 1
            STOP.wait(1)
        return 0
    finally:
        if night is not None:
            night_status.stop(night)
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    sys.exit(main())
