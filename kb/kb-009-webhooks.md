# KB-009: Webhook delivery failures
Webhooks notify your systems when alerts fire or scheduled reports complete. Configure them under
Settings > Integrations > Webhooks.

Delivery: Brightdesk sends a POST with a JSON body and expects a 2xx response within 10 seconds. Failed
deliveries are retried 5 times with exponential backoff over about 1 hour. After that the event is marked
failed; you can see and replay failed events in the webhook's Delivery log.

Each request is signed with the header X-Brightdesk-Signature (HMAC-SHA256 of the body using your webhook
secret). Signature mismatches are usually caused by re-serializing the JSON before verifying; verify the
raw request body.

A webhook endpoint is automatically disabled after 3 consecutive days of failures, and the workspace admins
are emailed. Re-enable it from the webhook settings once the endpoint is fixed.
