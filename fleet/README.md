# The fleet branch

This fork of [alexdlaird/amazon-orders-mcp](https://github.com/alexdlaird/amazon-orders-mcp) builds an image that
runs the server on a home server until our pull requests are released upstream. Its default branch, `fleet`, is:

- upstream's `develop`;
- merged with our pull-request branches that upstream hasn't released yet (each also open upstream);
- plus this directory and the two workflows `ci.yml` and `fleet-release.yml`.

Nothing else differs from upstream. When every pull request is released, the image installs upstream's release
from PyPI and only `fleet/` remains.

| Branch | Upstream pull request | What |
|---|---|---|
| `fix/sign-in-site-first` | [#5](https://github.com/alexdlaird/amazon-orders-mcp/pull/5) | `sign_in` checks the site before the display |
| `feat/search-orders` | not yet opened (needs amazon-orders#169 released) | A `search_orders` tool |

Several sites (#3), the container (#4) and the Streamable HTTP transport (#2) are in upstream's `develop`.

amazon-orders comes from our fork's `fleet-core` branch, pinned in `overrides.txt`, until upstream releases:
[alexdlaird/amazon-orders#168](https://github.com/alexdlaird/amazon-orders/pull/168) (VAT and addresses on English EU
pages) and [#169](https://github.com/alexdlaird/amazon-orders/pull/169) (Order search). `fleet-core` is upstream's
`develop` with both merged in.

## What's here

- `keeper.py`: the sign-in keeper. It runs beside the server, sharing its config directory (`HOME=/state`).
  - Every 2 hours it checks each site's saved session the way the server does.
  - A site Amazon no longer accepts is signed in again with amazon-orders' automated authentication. The
    credentials come from a 1Password LOGIN item, read with the keeper's own service-account token at sign-in
    time only.
  - At most 2 sign-ins per site per day.
  - A failed sign-in holds the site until `python -m fleet.keeper clear SITE`.
  - `python -m fleet.keeper status` prints the state; `check SITE` runs one check.
- `server.py`: the server's entry point (`python -m fleet.server`): upstream's CLI and server, plus a
  `keeper_status` tool that reports the keeper's last check of every site from its state file (no Amazon request).
  A monitor can call it as often as it likes; it fails when a site is held, signed out or not checked for 5 hours.
- `Dockerfile`: one image for both.
  - The server (`python -m fleet.server`, JSON replies) is the default command; the keeper is
    `python -m fleet.keeper run`.
  - It includes Chromium (for amazon-orders' `[browser]` extra) and the 1Password CLI (used by the keeper only).
  - It runs as uid 1000 with `HOME=/state`.
- `requirements.lock`: the locked dependencies, compiled with
  `uv pip compile pyproject.toml --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --override fleet/overrides.txt -o fleet/requirements.lock`
  (`overrides.txt` pins amazon-orders to `fleet-core` until upstream releases it; drop it and the flag then).
- `tests/`: the keeper's tests. CI runs them with upstream's tests and lint.

## Updating

1. `git fetch upstream && git merge upstream/develop`, then merge any updated pull-request branch. If a pull-request
   branch was rewritten (rebased upstream), rebuild instead: a new branch from `upstream/develop`, the open
   pull-request branches merged in, then `fleet/` and the two workflows; then merge `fleet` into it with
   `-s ours`, so `fleet` moves forward without a force-push.
2. Recompile `requirements.lock` if `pyproject.toml` changed.
3. Push to `fleet` (CI runs).
4. Tag `v<upstream version>-fleet.<n>`, e.g. `v1.4.0-fleet.3`. `fleet-release.yml` builds and smoke-tests
   `ghcr.io/benjamin-di/amazon-orders-mcp:<version>`.
