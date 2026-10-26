# Optional remote human sign-in: Amazon integration prototype

This review bundle demonstrates three small upstream extension points with a
real Chromium/noVNC viewer and a local synthetic provider. Amazon owns browser
selection, verification and session publication. The optional signin-window
integration supplies the human viewer and attempt lifecycle.

| Proposed interface | Patch | Upstream base |
|---|---|---|
| `PlaywrightBrowserLogin.launch_env()` | `01-browser-environment.patch` | amazon-orders `b6fdc880a8e2f54b7824a919d4bb91cc3d561bcf` |
| `AmazonSession.import_cookies(cookies)` | `02-verified-cookie-import.patch` | same base, after patch 01 |
| `SavedSession.sign_in_with(callback, site=None)` | `03-serialized-sign-in-adapter.patch` | amazon-orders-mcp `78a0c814830c367fbcc3e24f8e4e36daeb005262` |

The upstream patches add no signin-window dependency and preserve existing local
sign-in and tool schemas. They contain behavioral tests, documentation and
changelog entries. Interfaces are proposals for maintainer discussion.

`library-prototype.patch` adds embedded viewing, autonomous expiry/completion,
opt-in reload recovery, lifecycle regressions and `examples/amazon/` to the
published signin-window 0.4.4 source distribution. It is a development overlay,
not a release. The application retains its session: no cookie payload crosses a
viewer/result HTTP endpoint.

## Reproduce

Download the files from this directory together. Use Python 3.12, uv, Git and a Linux
browser environment with Xvfb, x11vnc and noVNC 1.6.0. Debian trixie is the CI
environment. The script installs Chromium's browser binary; system dependencies
must already be available (e.g. from `playwright install --with-deps chromium`).

```sh
NOVNC_DIR=/opt/novnc bash verify.sh
```

Set `SIGNIN_XVFB` and `SIGNIN_X11VNC` when those executables are outside PATH.
Set `VERIFY_OFFLINE=1` to also run the complete upstream offline suites and MCP
type/lint checks. All source, dependencies and state are isolated in a temporary
directory and removed on exit. The library source archive is SHA-256 verified;
upstream checkouts use the exact commits above. No shared checkout is changed.

The synthetic provider and browser traffic remain on loopback. Tests send input
through the actual noVNC canvas/keyboard. They use the original MCP tool list and
`check_session`, with one added `amazon_connect` tool.

## Evidence and limits

See `VALIDATION.md` for results, commands and scope. There is no real account,
password, order record or captured provider page in this bundle. Actual Amazon
login, mobile/password-manager behavior, restart reattachment and independent
concurrent publisher processes are not qualified. The cookie-file lock protects
threads; revision checks do not provide a cross-process transaction.
