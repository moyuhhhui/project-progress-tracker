import argparse
import logging
import sys
import time

from .app.reminders import run_cycle
from .app.store import Store


def _configure_console_encoding():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure:
            reconfigure(encoding='utf-8', errors='replace')


def main():
    _configure_console_encoding()
    parser = argparse.ArgumentParser(description='提醒检查进程；发送必须显式配置并开启')
    parser.add_argument('--once',action='store_true')
    args = parser.parse_args()
    store = Store()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    while True:
        try:
            logging.info('提醒检查结果：%s',run_cycle(store))
        except Exception as exc:
            logging.error('提醒检查失败（%s），未标记为已送达',type(exc).__name__)
        if args.once:
            return
        time.sleep(600)


if __name__ == '__main__':
    main()
