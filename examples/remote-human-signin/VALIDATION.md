# Synthetic validation evidence

Environment: Linux amd64, Python 3.12, Chromium, Xvfb, x11vnc and checksum-verified
noVNC 1.6.0. No Amazon account or live Amazon request is used by the tests.

| Check | Result |
|---|---|
| Library lint, format and offline suite | 232 passed; 3 optional browser tests skipped |
| New embedded lifecycle regressions | 5 passed, included above |
| Fresh-patch replay: amazon-orders offline suite | 619 passed, 6 skipped, 123 subtests passed |
| Fresh-patch replay: amazon-orders-mcp suite | 75 passed, 6 subtests passed |
| MCP mypy and source/test flake8 | Passed |
| Core source/test flake8 | Passed |
| Latest proposed core APIs' focused tests | 9 passed |
| Actual Chromium/noVNC acceptance | 5 passed |
| Mutation removing autonomous expiry | Deadline regression failed as expected |
| Exact integration source CI, including existing browser checks | Passed |

The five browser scenarios are:

1. Reload reconnects to one attempt; sign-in publishes a session reusable by the
   existing `check_session` MCP tool. MCP bearer checks and the tool list are tested.
2. Failed HTTP verification preserves the previous cookie jar.
3. Saving completes after the viewer page is closed.
4. Explicit Cancel closes the browser and eventually reaps owned resources, with
   no cookie jar saved.
5. An attempt superseded by a replacement link cannot publish over the prior jar.

The import tests additionally cover private file permissions, a fresh reader,
failed saving, a jar replaced during verification, foreign-cookie rejection
without a request, environment overrides that prevent staging, and clearing the
old signed-out state after successful import. The environment-hook tests cover
default/selected browser launch paths, unchanged parent environment and the Linux
display preflight. The MCP adapter tests cover a signed-out secondary site,
unverified completion, failure cleanup and serialized calls.

Fresh replay commands:

```sh
NOVNC_DIR=/opt/novnc bash verify.sh
VERIFY_OFFLINE=1 NOVNC_DIR=/opt/novnc bash verify.sh
```

The optional CAPTCHA extra was absent from the fresh full-suite replay. These
results establish synthetic mechanism behavior, not live Amazon compatibility,
account identity, cross-process atomic publication or upstream interface approval.
