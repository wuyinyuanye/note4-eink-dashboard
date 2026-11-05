import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from app import App, DEFAULTS

class RecoveryTests(unittest.TestCase):
    def test_connected_queries_status_before_first_image(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));app.connection='正在连接';app.awaiting_connect_image=True
            with patch.object(app,'send') as send,patch.object(app,'render') as render:
                app.bridge_event({'event':'connected','device':{'id':'0123456789ab'},'gray16':[],'gray4':[]})
                send.assert_called_once_with({'command':'status'})
                render.assert_not_called()
                app.bridge_event({'event':'status_error','error':'timeout'})
                render.assert_called_once_with(force=True,reason='connect')

    def test_idle_timeout_reconnects_but_inflight_timeout_stays_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d)); app.reconnect_enabled=True; app.connection='已连接'; app.inflight=None
            with patch('app.time.monotonic',return_value=100):
                app.bridge_event({'event':'disconnected','reason':'Timeout'})
                app.bridge_ended(0)
            self.assertFalse(app.blocked);self.assertEqual(app.reconnect_at,115);self.assertEqual(app.reconnect_attempts,1)
            with patch.object(app,'fetch'),patch.object(app,'connect') as connect:
                app.tick(monotonic_now=114);connect.assert_not_called()
                app.tick(monotonic_now=115);connect.assert_called_once()
            app.connection='已连接';app.inflight=7;app.blocked=False;app.reconnect_at=None
            app.bridge_event({'event':'disconnected','reason':'Timeout'});app.bridge_ended(0)
            self.assertTrue(app.blocked);self.assertIsNone(app.reconnect_at)

    def test_before_open_failure_retries_limited_and_disconnect_cancels(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d)); app.reconnect_enabled=True
            with patch('app.time.monotonic',return_value=100):
                for expected in (115,130,160):
                    app.connection='正在连接';app.inflight=0
                    app.bridge_ended(1);self.assertEqual(app.reconnect_at,expected)
                app.reconnect_at=None;app.connection='正在连接';app.inflight=0;app.bridge_ended(1)
            self.assertEqual(app.connection,'连接失败');self.assertIsNone(app.reconnect_at)
            app.reconnect_attempts=0;app.blocked=False;app.schedule_reconnect()
            app.disconnect();self.assertIsNone(app.reconnect_at);self.assertFalse(app.reconnect_enabled)

    def test_ambiguous_crash_and_gold_do_not_auto_reconnect(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));app.reconnect_enabled=True;app.connection='已连接';app.inflight=1
            app.bridge_ended(1);self.assertTrue(app.blocked);self.assertIsNone(app.reconnect_at)
            app.blocked=False;app.page='gold';app.schedule_reconnect();self.assertIsNone(app.reconnect_at)
