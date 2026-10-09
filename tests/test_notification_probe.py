"""Offline no-email regression checks; no OCI credentials or network required."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'backend/app/oci/studio_notification_probe.py'
spec = importlib.util.spec_from_file_location('notification_probe', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NotificationProbeTests(unittest.TestCase):
    def inspect(self, subscriptions=None, denied=False):
        control, data = Mock(), Mock()
        control.get_topic.return_value = NS(data=NS(lifecycle_state='ACTIVE',
            api_endpoint='https://example.invalid', compartment_id='scope'))
        data.list_subscriptions.return_value = NS(data=subscriptions or [], headers={})
        if denied:
            error = RuntimeError('private-value-must-not-escape'); error.status = 403
            data.list_subscriptions.side_effect = error
        oci = NS(ons=NS(NotificationControlPlaneClient=Mock(return_value=control),
            NotificationDataPlaneClient=Mock(return_value=data)), retry=NS(NoneRetryStrategy=lambda: None))
        client = NS(auth_mode='resource_principal', enabled=True, shared_poc_enabled=True,
            topic_id='synthetic-topic', _credentials=lambda: ({}, {'signer': 'not-exported'}))
        with patch.dict('sys.modules', {'oci': oci}):
            probe = module.NotificationProbe(lambda: client)
            result = probe.check()
            self.assertEqual(result, probe.check())
        data.publish_message.assert_not_called()
        self.assertNotIn('private-value-must-not-escape', str(result))
        self.assertNotIn('not-exported', str(result))
        return result

    def test_active_subscriber(self):
        result = self.inspect([NS(protocol='EMAIL', lifecycle_state='ACTIVE')])
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['publish'], 'not_tested')

    def test_pending_confirmation_preserves_access_evidence(self):
        result = self.inspect([NS(protocol='EMAIL', lifecycle_state='PENDING')])
        self.assertEqual(result['topic_read'], 'passed')
        self.assertEqual(result['subscription_list'], 'passed')
        self.assertEqual(result['pending_email_subscriptions'], 1)
        self.assertEqual(result['reason'], 'no_active_email_subscription')

    def test_permission_rejection_is_redacted(self):
        self.assertEqual(self.inspect(denied=True)['status'], 'authorization_or_visibility_rejected')


if __name__ == '__main__':
    unittest.main()
