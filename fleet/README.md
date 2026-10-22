# The fleet branch

This fork of [alexdlaird/amazon-orders-mcp](https://github.com/alexdlaird/amazon-orders-mcp) builds an image that
runs the server on a home server until our pull requests are released upstream. Its default branch, `fleet`, is:

- upstream's `develop`;
- merged with our pull-request branches that upstream hasn't released yet (each also open upstream);
- plus this directory and the two workflows `ci.yml` and `fleet-release.yml`.

Nothing else differs from upstream. When every pull request is released, the image installs upstream's release
from PyPI and only `fleet/` remains.

`carried.txt` lists what the build carries on top of upstream: each item's upstream pull request and our branch.
`python fleet/upstream.py` (needs the `gh` CLI) reports for each whether upstream has merged and released it, and
exits 1 when something can be dropped. Today that's Order search in amazon-orders
([#169](https://github.com/alexdlaird/amazon-orders/pull/169)), and the `search_orders` tool built on it, whose pull
request follows #169's release.

amazon-orders comes from our fork's `fleet-core` branch: upstream's `develop` with the carried amazon-orders pull
requests merged in. `overrides.txt` pins one commit of it, which a `fleet-core-<date>` tag keeps, so the pin can be
fetched even after `fleet-core` is rebuilt. Never delete a tag a pin uses.

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

Fetch first (`git fetch origin upstream`) and build on `origin/fleet`: other changes land on `fleet` too.

1. **Upstream moved:** `git merge upstream/develop` into `fleet`.
2. **A carried branch changed:** merge it into `fleet`. If it was rewritten (rebased for upstream), rebuild instead:
   1. a new branch from `upstream/develop`;
   2. merge each open branch in `carried.txt`;
   3. bring `fleet/` and the two workflows from `origin/fleet`;
   4. `git merge -s ours origin/fleet`, so `fleet` moves forward without a force-push.
3. **The amazon-orders pin changed:** rebuild `fleet-core` (upstream's `develop` plus the carried amazon-orders
   branches), push it, tag the commit `fleet-core-<date>`, point `overrides.txt` at it, and recompile
   `requirements.lock` with the command above. Recompile too if `pyproject.toml` changed.
4. **Something was released upstream** (`python fleet/upstream.py` exits 1): remove it from `carried.txt` and the
   build. When no amazon-orders item is left, delete `overrides.txt` and drop `--override` from the compile command.
5. Push to `fleet` (CI runs). A change to what the image runs is released by tagging `v<upstream version>-fleet.<n>`,
   e.g. `v1.4.2-fleet.1`. `fleet-release.yml` builds and smoke-tests `ghcr.io/benjamin-di/amazon-orders-mcp:<version>`.
   A change to this directory's documentation or tooling alone needs no release.
