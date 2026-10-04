# KB-002: Troubleshooting SSO / SAML sign-in
SSO is available on Business and Enterprise plans and is configured by a workspace admin under
Settings > Security > Single sign-on.

Common errors:
- "SAML response signature invalid": the certificate in Brightdesk does not match your IdP. Upload the
  current signing certificate; IdP certificates often rotate yearly.
- "User not provisioned": the user's email is not in the workspace and just-in-time provisioning is
  off. Invite the user or enable JIT provisioning.
- "Audience mismatch": the Entity ID in your IdP must be exactly https://app.brightdesk.example/saml/metadata.
- Redirect loop after sign-in: clear cookies for brightdesk.example, or try a private window.

Admins can always sign in with their password at app.brightdesk.example/login?sso=bypass to fix a broken
SSO configuration. When contacting support, include the SAML trace (browser extension "SAML-tracer") and
the time of the failed attempt.
