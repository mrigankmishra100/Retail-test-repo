"""Read-only validation of the configured shared POC topic; never publishes."""
import json
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from dotenv import dotenv_values
from app.oci.notifications import OCINotificationClient, NotificationDeliveryError

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--env-file', required=True)
parser.add_argument('--topic-id', required=True)
args = parser.parse_args()
configuration = {**dotenv_values(args.env_file), **os.environ}
notifications = OCINotificationClient(
    topic_id=args.topic_id, enabled=True, shared_poc_enabled=True,
    auth_mode=configuration.get('OCI_AUTH_MODE') or 'api_key',
    config_file=configuration.get('OCI_CONFIG_FILE') or '~/.oci/config',
    config_profile=configuration.get('OCI_CONFIG_PROFILE') or 'DEFAULT',
)
try:
    notifications.ensure_ready()
    client = notifications._get_client()  # Reads topic metadata, no publish call.
    audience = notifications._verify_email_subscription(client, None, shared_poc=True)
    print(json.dumps({
        "status": "shared_topic_ready", "read_only": True,
        "shared_poc_enabled": notifications.shared_poc_enabled,
        "publishing_enabled": notifications.enabled,
        "active_email_subscriptions": audience["active"],
        "pending_email_subscriptions": audience["pending"],
        "notification_sent": False,
    }))
except NotificationDeliveryError as exc:
    print(json.dumps({"status": "not_ready", "reason": exc.code, "notification_sent": False}))
    raise SystemExit(1)
finally:
    notifications.close()
