"""Exercise the staging supervisor without starting services or contacting hardware."""
import importlib.util
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).parent / 'deploy/nas'))
spec = importlib.util.spec_from_file_location('night_entrypoint', Path(__file__).parent / 'deploy/nas/entrypoint.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class NightWindowTests(unittest.TestCase):
    def test_night_page_remains_readable_but_rejects_commands(self):
        instance = entry.night_status.start('127.0.0.1', 0)
        url = f'http://127.0.0.1:{instance[0].server_port}'
        try:
            with urllib.request.urlopen(url + '/', timeout=2) as response:
                self.assertIn('夜间暂停中', response.read().decode())
            with urllib.request.urlopen(url + '/api/state', timeout=2) as response:
                self.assertEqual(json.load(response)['mode'], 'night')
            request = urllib.request.Request(url + '/api/action', data=b'{}', method='POST')
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=2)
            self.assertEqual(error.exception.code, 423)
            error.exception.close()
        finally:
            entry.night_status.stop(instance)

    def test_boundaries_are_utc8_even_when_input_is_utc(self):
        self.assertFalse(entry.is_active(datetime(2026, 11, 4, 16, 0, tzinfo=timezone.utc)))
        self.assertFalse(entry.is_active(datetime(2026, 11, 4, 21, 59, 59, tzinfo=timezone.utc)))
        self.assertTrue(entry.is_active(datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc)))
        self.assertTrue(entry.is_active(datetime(2026, 11, 4, 15, 59, 59, tzinfo=timezone.utc)))

    def test_night_start_never_launches_and_dawn_starts_once(self):
        stop = Mock()
        stop.is_set.side_effect = [False, False, False, True]
        child = Mock()
        child.poll.return_value = None
        clock = Mock()
        clock.now.side_effect = [datetime(2026, 11, 5, 0, 0, tzinfo=entry.TZ),
                                 datetime(2026, 11, 5, 5, 59, tzinfo=entry.TZ),
                                 datetime(2026, 11, 5, 6, 0, tzinfo=entry.TZ)]
        with patch.object(entry, 'STOP', stop), patch.object(entry, 'datetime', clock), \
             patch.object(entry.signal, 'signal'), patch.object(entry.night_status, 'start', return_value=object()), \
             patch.object(entry.night_status, 'stop'), patch.object(entry.subprocess, 'Popen', return_value=child) as popen:
            self.assertEqual(entry.main(), 0)
        popen.assert_called_once()
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=15)

    def test_midnight_stops_and_reaps_before_next_start(self):
        stop = Mock()
        stop.is_set.side_effect = [False, False, False, True]
        first, second = Mock(), Mock()
        first.poll.return_value = second.poll.return_value = None
        clock = Mock()
        clock.now.side_effect = [datetime(2026, 11, 4, 23, 59, 59, tzinfo=entry.TZ),
                                 datetime(2026, 11, 5, 0, 0, tzinfo=entry.TZ),
                                 datetime(2026, 11, 5, 6, 0, tzinfo=entry.TZ)]
        first.wait.side_effect = [subprocess.TimeoutExpired('app', 15), 0]
        with patch.object(entry, 'STOP', stop), patch.object(entry, 'datetime', clock), \
             patch.object(entry.signal, 'signal'), patch.object(entry.night_status, 'start', return_value=object()), \
             patch.object(entry.night_status, 'stop'), patch.object(entry.subprocess, 'Popen', side_effect=[first, second]):
            self.assertEqual(entry.main(), 0)
        first.terminate.assert_called_once()
        first.kill.assert_called_once()
        self.assertEqual(first.wait.call_args_list, [call(timeout=15), call()])
        second.terminate.assert_called_once()

    def test_unexpected_daytime_exit_is_reported(self):
        stop = Mock()
        stop.is_set.return_value = False
        child = Mock()
        child.poll.return_value = 1
        clock = Mock()
        clock.now.return_value = datetime(2026, 11, 5, 12, tzinfo=entry.TZ)
        with patch.object(entry, 'STOP', stop), patch.object(entry, 'datetime', clock), \
             patch.object(entry.signal, 'signal'), patch.object(entry.night_status, 'start', return_value=object()), \
             patch.object(entry.night_status, 'stop'), patch.object(entry.subprocess, 'Popen', return_value=child):
            self.assertEqual(entry.main(), 1)
        child.terminate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
