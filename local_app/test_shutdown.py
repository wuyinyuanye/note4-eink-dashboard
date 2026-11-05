import subprocess
import threading
import unittest
from unittest.mock import Mock, call

from app import App


class ShutdownTests(unittest.TestCase):
    def test_unresponsive_bridge_is_reaped_after_close(self):
        app = App.__new__(App)
        app.stop = threading.Event()
        app.stop.set()
        app.send = Mock()
        app.process = Mock()
        app.process.wait.side_effect = [subprocess.TimeoutExpired('bridge', 4),
                                        subprocess.TimeoutExpired('bridge', 1), 0]
        app.run()
        app.send.assert_called_once_with({'command': 'close'})
        app.process.terminate.assert_called_once_with()
        app.process.kill.assert_called_once_with()
        self.assertEqual(app.process.wait.call_args_list,
                         [call(timeout=4), call(timeout=1), call()])

    def test_bridge_exit_after_close_needs_no_signal(self):
        app = App.__new__(App)
        app.stop = threading.Event()
        app.stop.set()
        app.send = Mock()
        app.process = Mock()
        app.run()
        app.process.terminate.assert_not_called()
        app.process.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
