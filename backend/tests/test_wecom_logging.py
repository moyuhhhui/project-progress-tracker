import io
import unittest
from contextlib import redirect_stdout

from backend.wecom_worker import QuietSDKLogger


class WeComLoggingTests(unittest.TestCase):
    def test_sdk_diagnostics_keep_events_but_omit_payloads_and_credentials(self):
        output = io.StringIO()
        logger = QuietSDKLogger()
        with redirect_stdout(output):
            logger.debug('Received push message: private-message-secret')
            logger.debug('Received heartbeat ack')
            logger.error('Reply ack error: reqId=private-id, errcode=6000, errmsg=secret')
            logger.error('unknown secret error')
        log = output.getvalue()
        self.assertIn('message_received', log)
        self.assertIn('heartbeat_ack', log)
        self.assertIn('reply_rejected', log)
        self.assertNotIn('private', log)
        self.assertNotIn('secret', log)
