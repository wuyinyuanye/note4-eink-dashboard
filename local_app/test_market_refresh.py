"""Quote-age transitions must never produce separate screen submissions."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from app import App,DEFAULTS
from test_market import NOW,quote
from market import parse_quotes


class RefreshTests(unittest.TestCase):
    def test_staggered_five_minute_expiry_sends_only_completed_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));app.page='market'
            rows=parse_quotes(quote(stamp=NOW-timedelta(seconds=5))+quote('sz399001',stamp=NOW-timedelta(seconds=3)),app.market.codes,NOW)
            app.market.accept(rows,NOW);app.render(now=NOW)
            app.connection='已连接';app.pending=False;app.inflight=None
            with patch.object(app,'fetch'),patch.object(app,'fetch_market') as fetch,patch.object(app,'send') as send:
                for second in (295,296,297,298,299):
                    app.tick(NOW+timedelta(seconds=second),second)
                send.assert_not_called();fetch.assert_not_called()
                with patch.object(app.market,'started',wraps=app.market.started):
                    # Emulate the real fetch method setting its in-progress gate.
                    fetch.side_effect=app.market.started
                    app.tick(NOW+timedelta(seconds=300),300)
                    app.tick(NOW+timedelta(seconds=301),301)
                fetch.assert_called_once();send.assert_not_called()
                fresh=parse_quotes(quote(price='3510',stamp=NOW+timedelta(minutes=5)),app.market.codes,NOW+timedelta(minutes=5))
                app.market_result((fresh,NOW+timedelta(minutes=5),None))
                send.assert_called_once()
                self.assertEqual(app.inflight_reason,'market')

    def test_session_boundary_and_failed_fetch_do_not_queue_age_updates(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));app.page='market'
            noon=NOW.replace(hour=12,minute=59,second=59)
            rows=parse_quotes(quote(),app.market.codes,NOW)
            app.market.accept(rows,noon);app.render(now=noon)
            app.pending=False;app.connection='已连接'
            with patch.object(app,'fetch'),patch.object(app,'fetch_market'),patch.object(app,'send') as send:
                app.tick(noon+timedelta(seconds=1),1);send.assert_not_called()
                app.market_result((None,noon+timedelta(seconds=2),'offline'))
                send.assert_called_once()
                app.inflight=None
                for second in (10,61,120):app.tick(noon+timedelta(seconds=second),second)
                self.assertEqual(send.call_count,1)


if __name__=='__main__':unittest.main()
