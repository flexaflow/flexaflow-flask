# Security

## What is protected, and how

| Area | Behaviour |
|---|---|
| CSRF | Every POST/PUT/PATCH/DELETE needs the session's token (form field `csrf_token` or header `X-CSRF-Token`). |
| Editor HTML | Post, page and richtext-field HTML is cleaned on save with an allow-list (`migrate_utils.sanitize_html`). Scripts, event handlers and `javascript:` URLs are removed. Iframes are kept only for YouTube, Vimeo, Google Maps, Spotify and SoundCloud. Content saved by older versions is not rewritten; re-save it to clean it. |
| Login throttling | Failures are stored in the database (`login_attempts`), so limits survive restarts and work across workers. They are counted per IP and per username+IP, never per username alone, so a stranger cannot lock a real user out. After a free allowance each further failure doubles the wait (up to 15 minutes). TOTP codes have their own counter. |
| Two-factor | TOTP secrets are encrypted at rest with Fernet. The key is `FLEXAFLOW_2FA_KEY`, or `instance/twofa.key` if unset. Eight one-time recovery codes are issued when 2FA is enabled; only their hashes are stored. |
| Sessions | Changing a password, or an administrator changing a user's role/status/password, signs out that user's other sessions. |
| Headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, a CSP limited to `frame-ancestors`, `object-src`, `base-uri` and `form-action`, and HSTS when `SESSION_COOKIE_SECURE=1`. |
| Proxy | Set `FLEXAFLOW_BEHIND_PROXY=1` behind nginx so client IPs and HTTPS are read correctly. Do not set it when the app is reachable directly, or clients can spoof their IP. |
| Errors | Upload and other handlers log details and return generic messages. |
| Audit log | Logins, failures, logouts, password and role changes, user creation/deletion and 2FA changes are written to the `flexaflow.security` logger and to `instance/security.log`. |

The CSP does not restrict `script-src` or `style-src`, because the admin loads TinyMCE and other assets from CDNs and uses inline scripts. If you self-host those assets you can tighten the policy in `_security_headers` in `app.py`.

## Extensions and themes run Python

**Extensions** (`extensions/<slug>/extension.py`) **and themes** (`themes/<name>/functions/*.py`) are executed by the server with the same rights as the CMS. A malicious zip can read your database, your secret keys and your files.

- Only administrators can install them.
- Install only packages you wrote or trust, and read the code first.
- The admin shows a trust warning and a SHA-256 of the package before an extension or theme is activated.
- Set `FLEXAFLOW_ALLOW_EXTENSION_UPLOAD=0` to disable browser uploads on a production server and install from the command line instead.

## Reporting a vulnerability

Please open a private security advisory on the GitHub repository rather than a public issue.
