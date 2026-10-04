# KB-008: Data API authentication and rate limits
The Data API lets you query dashboards and datasets programmatically. Create API keys under Settings >
API keys; keys inherit the permissions of the user who created them.

Rate limits per workspace:
- Starter: 60 requests per minute
- Business: 300 requests per minute
- Enterprise: 1,000 requests per minute (higher on request)

When you exceed the limit the API returns HTTP 429 with a Retry-After header (seconds). Clients should
wait that long and retry with exponential backoff. Bursts are smoothed over a 10-second window.

HTTP 401 means the key is missing, revoked, or malformed (send it as "Authorization: Bearer <key>").
HTTP 403 means the key's user lacks access to that dataset.

Never share API keys in support tickets. If a key was exposed, revoke it immediately under Settings >
API keys and create a new one.
