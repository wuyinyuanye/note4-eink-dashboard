import io
import tempfile
import threading
import unittest
from pathlib import Path
import json
from app import save_bounded_stderr, append_display_history


class BridgeLogTests(unittest.TestCase):
    def test_history_rollover_keeps_complete_recent_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.jsonl'
            for i in range(40): append_display_history(path,{'index':i,'text':'灰'*8},limit=256)
            self.assertLessEqual(path.stat().st_size,256)
            rows=[json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows[-1]['index'],39)
            self.assertTrue(all(a['index']<b['index'] for a,b in zip(rows,rows[1:])))
    def test_stderr_is_held_in_memory_until_exit_and_saved_as_a_bounded_tail(self):
        class Stream:
            def __init__(self):
                self.ready=threading.Event();self.release=threading.Event();self.first=True
            def read(self, size):
                if self.first:
                    self.first=False;self.ready.set();return 'a'*70000
                self.release.wait(2);return ''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bridge.stderr.log';stream=Stream()
            thread=threading.Thread(target=save_bounded_stderr,args=(stream,path))
            thread.start();self.assertTrue(stream.ready.wait(2))
            self.assertFalse(path.exists())
            stream.release.set();thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(path.read_text(),'a'*65536)


if __name__=='__main__': unittest.main()
