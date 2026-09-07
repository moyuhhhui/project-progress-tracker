"""显式启动的企业微信智能机器人接收进程；不由 Web 服务自动启动。"""
import argparse
import asyncio
import importlib.util
import json
import os
import secrets
from datetime import datetime, timezone

from .app import ai
from .app.service import BusinessError, Service, require
from .app.store import Store
from .app.wecom import BotHandler, BotRuntime, validate_web_url


class QuietSDKLogger:
    # SDK 原始日志可能含回调/凭证；仅输出白名单事件名。
    def debug(self, message, *args):
        events = {
            'Authentication successful': 'authenticated',
            'Received heartbeat ack': 'heartbeat_ack',
            'Received push message:': 'message_received',
            'Received event callback:': 'event_received',
            'Reply ack received for reqId:': 'reply_ack',
            'Reply ack error:': 'reply_rejected',
            'Heartbeat ack error:': 'heartbeat_rejected',
            'WebSocket error:': 'connection_error',
        }
        for prefix, event in events.items():
            if isinstance(message, str) and message.startswith(prefix):
                print(datetime.now(timezone.utc).isoformat(), event, flush=True)
                break

    info = debug
    warn = debug
    error = debug


async def run_bot(service, bot_id, web_url, client, stop=None, parser=None):
    handler = BotHandler(service, bot_id, web_url)
    runtime = BotRuntime(service, bot_id)
    stop = stop or asyncio.Event()
    phase, tasks = 'connecting', set()

    def set_phase(value):
        nonlocal phase
        phase = value
        runtime.update(phase)

    async def message(frame):
        body = frame.get('body') if isinstance(frame, dict) else None
        headers = frame.get('headers') if isinstance(frame, dict) else None
        if (not isinstance(body, dict) or not isinstance(headers, dict)
                or not isinstance(headers.get('req_id'), str) or not headers['req_id']
                or frame.get('cmd') != 'aibot_msg_callback' or body.get('aibotid') != bot_id
                or body.get('chattype') != 'group'):
            print('message_ignored', 'body_valid=', isinstance(body, dict),
                  'headers_valid=', isinstance(headers, dict),
                  'bot_matches=', isinstance(body, dict) and body.get('aibotid') == bot_id,
                  'is_group=', isinstance(body, dict) and body.get('chattype') == 'group', flush=True)
            return
        print('message_accepted', flush=True)
        task = asyncio.current_task()
        tasks.add(task)
        stream_id = secrets.token_hex(16)
        try:
            # 先完成平台应答；耗时模型调用在线程中运行，不阻塞 SDK 心跳。
            await client.reply_stream(frame, stream_id, '正在整理草稿，信息齐全后自动保存；缺少的信息会提示补充。', False)
            reply = await handler.handle(frame, parser)
            await client.reply_stream(frame, stream_id, reply or '无法识别消息身份，未修改数据。', True)
        except Exception as exc:
            set_phase('error')
            print('message_failed', type(exc).__name__, flush=True)
            print('企业微信消息处理或回复失败，请登录网页检查草稿；未自动重发。', flush=True)
        finally:
            tasks.discard(task)

    client.on('authenticated', lambda: set_phase('authenticated'))
    client.on('disconnected', lambda *_: set_phase('disconnected'))
    client.on('reconnecting', lambda *_: set_phase('connecting'))
    client.on('error', lambda *_: set_phase('error'))
    client.on('message', message)
    runtime.acquire()
    try:
        await client.connect()
        while not stop.is_set():
            runtime.update(phase)
            try:
                await asyncio.wait_for(stop.wait(), timeout=20)
            except asyncio.TimeoutError:
                pass
    finally:
        client.disconnect()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        runtime.close()


def configuration_check():
    checks = {
        'bot_enabled': os.getenv('TRACKER_WECOM_BOT_ENABLED') == 'true',
        'bot_id_configured': bool(os.getenv('WECOM_BOT_ID', '').strip()),
        'bot_secret_configured': bool(os.getenv('WECOM_BOT_SECRET', '').strip()),
        'sdk_installed': importlib.util.find_spec('aibot') is not None,
        'ai_configured': ai.configured(),
    }
    try:
        validate_web_url(os.getenv('TRACKER_WEB_URL', ''))
        checks['web_url_configured'] = True
    except BusinessError:
        checks['web_url_configured'] = False
    return checks


def main():
    args_parser = argparse.ArgumentParser(description='企业微信群内 @机器人接收进程')
    args_parser.add_argument('--check', action='store_true', help='只检查配置及依赖；不联网、不写数据库、不输出密钥')
    args = args_parser.parse_args()
    checks = configuration_check()
    if args.check:
        print(json.dumps(checks, ensure_ascii=False))
        return 0 if all(checks.values()) else 2
    try:
        require(checks['bot_enabled'], '请显式设置 TRACKER_WECOM_BOT_ENABLED=true 后启动')
        require(checks['sdk_installed'], '缺少官方 SDK，请先按 README 安装 requirements-wecom.txt')
        require(checks['bot_id_configured'] and checks['bot_secret_configured'], '请配置 WECOM_BOT_ID 和 WECOM_BOT_SECRET')
        require(checks['web_url_configured'], '请配置员工可访问的 TRACKER_WEB_URL 根地址')
        require(checks['ai_configured'], '请先配置并开启 AI；网页手动录入不受影响')
        from aibot import WSClient, WSClientOptions
        client = WSClient(WSClientOptions(bot_id=os.environ['WECOM_BOT_ID'], secret=os.environ['WECOM_BOT_SECRET'],
                                         max_reconnect_attempts=-1, logger=QuietSDKLogger()))
        print('正在启动企业微信接收进程；请在管理页面查看连接状态。', flush=True)
        asyncio.run(run_bot(Service(Store()), os.environ['WECOM_BOT_ID'], os.environ['TRACKER_WEB_URL'], client))
        return 0
    except BusinessError as exc:
        print(exc.message)
        return 2
    except KeyboardInterrupt:
        print('企业微信接收进程已停止。')
        return 0
    except Exception:
        print('企业微信接收进程启动失败，请检查 SDK 版本、网络和机器人配置；原始错误未输出。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
