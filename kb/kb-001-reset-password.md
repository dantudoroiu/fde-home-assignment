# KB-001: Resetting your password
Any user can reset their own password from the sign-in page.

1. Go to app.brightdesk.example/login and click "Forgot password".
2. Enter the email address of your Brightdesk account.
3. Open the reset email (sent from no-reply@brightdesk.example) and follow the link within 60 minutes.

If the email does not arrive within 10 minutes, check spam and any corporate email quarantine. Reset links
expire after 60 minutes and can only be used once.

If your organization signs in with SSO, passwords are managed by your identity provider (Okta, Azure AD,
Google Workspace) and the "Forgot password" flow is disabled. Contact your IT administrator instead.

After 5 failed sign-in attempts an account is locked for 15 minutes. Workspace admins can unlock users
immediately from Settings > Users.
