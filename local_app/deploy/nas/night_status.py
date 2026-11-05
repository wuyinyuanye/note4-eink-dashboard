"""Read-only control page while the NOTE4 application is paused overnight."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>NOTE4 · 夜间休息</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f5f4ef;color:#27332f;font:15px -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;line-height:1.65}main{max-width:640px;margin:9vh auto;padding:0 20px}.eyebrow{font-size:11px;letter-spacing:2px;color:#6b746d}article{margin-top:20px;background:#fffefa;border:1px solid #dedfd6;border-radius:16px;padding:32px}h1{font-size:26px;margin:0 0 8px}p{margin:10px 0;color:#657068}.status{display:inline-block;border-radius:20px;padding:5px 12px;background:#edf2e8;color:#3c5448;font-size:12px}.detail{border-top:1px solid #e4e5dd;margin-top:24px;padding-top:18px;font-size:13px}@media(max-width:480px){article{padding:24px}h1{font-size:23px}}</style>
<main><div class="eyebrow">NOTE4 / 桌面信息屏</div><article><span class="status">夜间暂停中</span><h1>让屏幕安静休息</h1><p>00:00–06:00 暂停取数、轮播和设备更新。</p><div class="detail"><p>06:00 后应用会自动恢复，连接与显示状态以恢复后的控制台为准。</p><p>当前页面仅显示状态，夜间操作暂不可用。</p></div></article></main></html>'''.encode('utf-8')


def handler(port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, code, body, mime):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def trusted(self):
            local = self.connection.getsockname()[0]
            actual_port = self.server.server_port
            return self.headers.get('Host') in {f'{host}:{actual_port}' for host in (local, '127.0.0.1', 'localhost')}
        def do_GET(self):
            if not self.trusted(): return self.reply(403, b'Forbidden', 'text/plain')
            if self.path == '/': return self.reply(200, PAGE, 'text/html; charset=utf-8')
            if self.path == '/api/state':
                body = json.dumps({'mode': 'night', 'message': '夜间暂停，06:00 后恢复'}, ensure_ascii=False).encode()
                return self.reply(200, body, 'application/json; charset=utf-8')
            return self.reply(404, b'Not found', 'text/plain')
        def do_POST(self):
            if not self.trusted(): return self.reply(403, b'Forbidden', 'text/plain')
            body = json.dumps({'error': '夜间暂停，06:00 后恢复'}, ensure_ascii=False).encode()
            return self.reply(423, body, 'application/json; charset=utf-8')
    return Handler


def start(bind, port):
    server = ThreadingHTTPServer((bind, port), handler(port))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop(instance):
    server, thread = instance
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
