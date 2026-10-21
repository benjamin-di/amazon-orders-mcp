<p align="center"><img alt="amazon-orders-mcp - An MCP server for Amazon order history" src="https://amazon-orders.readthedocs.io/_images/logo.png" /></p>

[![Version](https://img.shields.io/pypi/v/amazon-orders-mcp)](https://pypi.org/project/amazon-orders-mcp)
[![Python Versions](https://img.shields.io/pypi/pyversions/amazon-orders-mcp.svg)](https://pypi.org/project/amazon-orders-mcp)
[![Build](https://img.shields.io/github/actions/workflow/status/alexdlaird/amazon-orders-mcp/build.yml)](https://github.com/alexdlaird/amazon-orders-mcp/actions/workflows/build.yml)
[![GitHub License](https://img.shields.io/github/license/alexdlaird/amazon-orders-mcp)](https://github.com/alexdlaird/amazon-orders-mcp/blob/main/LICENSE)

<!-- mcp-name: io.github.alexdlaird/amazon-orders-mcp -->

`amazon-orders-mcp` is an unofficial [MCP](https://modelcontextprotocol.io) server for Amazon order history, line items,
and transactions, built on [`amazon-orders`](https://github.com/alexdlaird/amazon-orders). It gives your AI assistant
tools for your Amazon account, available from Claude and any other MCP client.

The server never sees your credentials. When you sign in, a browser window opens on Amazon's own page, and the server
reuses the session that leaves behind. Signing in needs a display, so run the server on the machine you sign in on. On a
machine without one, like a server or a container, sign in with
[`amazon-orders`](https://github.com/alexdlaird/amazon-orders) instead, as shown in
[Running in a Container](#running-in-a-container).

> **Note:** This server reads its data through `amazon-orders`, which works by parsing data from Amazon's
> consumer-facing website. As Amazon provides no official API to use, older versions may break at any time, so it's
> recommended that you use the latest version.

## Installation

`amazon-orders-mcp` is available on [PyPI](https://pypi.org/project/amazon-orders-mcp/) and is run
with [`uvx`](https://docs.astral.sh/uv/), so there's nothing to install up front. Add it to your AI assistant:

### Claude

**Claude Desktop:** download `amazon-orders-mcp-<version>.mcpb` from the
[latest release](https://github.com/alexdlaird/amazon-orders-mcp/releases/latest) and open it.

**Claude Code:**

```sh
claude mcp add amazon-orders -- uvx amazon-orders-mcp
```

### ChatGPT

**ChatGPT Desktop:** in Settings → Plugins, add an MCP server of type STDIO, with `uvx` as the command and
`amazon-orders-mcp` as its argument.

**Codex:**

```sh
codex mcp add amazon-orders -- uvx amazon-orders-mcp
```

### Other Clients

Any client that runs local MCP servers can use this JSON, or `amazon-orders-mcp` as the command if you've
installed it with `pip install amazon-orders-mcp`:

```json
{
  "mcpServers": {
    "amazon-orders": {
      "command": "uvx",
      "args": ["amazon-orders-mcp"]
    }
  }
}
```

### Remote Clients

For clients that connect to a server instead of running one, the server can also serve
[Streamable HTTP](https://modelcontextprotocol.io/specification/latest/basic/transports/streamable-http):

```sh
uvx amazon-orders-mcp --transport streamable-http
```

It listens at `http://127.0.0.1:8000/mcp` (change that with `--host` and `--port`), and answers `GET /health` with
`ok`, without calling Amazon, for health checks. A call's progress is reported on an event stream, unless
`--json-response` is passed, which answers each request with JSON instead, for a proxy that doesn't stream.

> **Warning:** The server has no sign-in of its own: anyone who can reach it can use its tools with the Amazon account
> it's signed in to. Only listen on `127.0.0.1`, or behind a reverse proxy that authenticates clients. On `127.0.0.1`,
> only requests addressed to `127.0.0.1` or `localhost` are accepted, so a proxy on the same machine must pass that as
> the `Host`.

To also have the server check a bearer token, put it in a file and pass `--token-file <path>`, or set
`AMAZON_ORDERS_MCP_TOKEN`. Every request but `/health` must then carry `Authorization: Bearer <token>`.

`sign_in` still opens its browser window on the machine the server runs on.

## Basic Usage

Just ask your assistant about your orders, and it will use the following tools:

| Tool                     | Returns                                                                    |
|--------------------------|----------------------------------------------------------------------------|
| `sign_in`                | Opens a browser window for you to sign in to Amazon, when you ask          |
| `get_order_history`      | A batch of orders by year, time filter or order type, optionally detailed  |
| `get_order`              | One order, by order number                                                 |
| `get_tracking`           | A shipment's carrier and tracking number, from an order's shipment ID      |
| `get_invoice_pdfs`       | An order's invoice and credit note PDFs, where the seller issues them      |
| `get_transactions`       | A batch of payment transactions over a number of days, or for an order     |
| `get_gift_card_balance`  | The current Gift Card balance                                              |
| `get_gift_card_activity` | Gift Card activity over a number of days                                   |
| `get_prime_payments`     | Prime membership fee charges, each with its order number                   |
| `get_family_members`     | Amazon Household members whose orders you can view, like Teen accounts     |
| `get_config`             | The settings your assistant can change, and their values                   |
| `update_config`          | Changes a setting, like the default Amazon site (e.g. `amazon.co.uk`)      |
| `check_session`          | Whether you're signed in, on each Amazon site you have a session for       |

Order history and transactions come back in batches of `limit` results (25 by default, and a batch can run up to a
page over), with a `cursor` your assistant passes back to get the next batch.

Every tool that reads Amazon takes an optional `site`, like `amazon.de`, so your assistant can read each Amazon site
you shop on from one server, e.g. "my last amazon.de orders". Without it, tools read your default site, the `domain`
setting.

### Configuration

Ask your assistant to change a setting, such as the default Amazon site (e.g. `amazon.co.uk`) or the browser to sign in
with. Settings are shared with the `amazon-orders` CLI, so it follows the same changes. See
[Supported Sites](https://amazon-orders.readthedocs.io/language-packages.html#supported-sites) for the sites
`amazon-orders` can read.

### Several Amazon Sites

Each Amazon site has its own orders and its own sign-in, so the server keeps a saved session for each site you sign in
to. Ask your assistant to sign you in to a site, and it calls `sign_in` with that `site`. Each session is saved in its
own cookie jar:

- The default site's is `cookie_jar_path` (by default, `~/.config/amazonorders/cookies.json`), where the
  `amazon-orders` CLI saves it, as before.
- Every other site's is next to it, named for the site: `cookies.amazon.de.json`, `cookies.amazon.co.uk.json`, and so
  on.

To sign in to another site from a terminal, run `amazon-orders --domain amazon.de login --browser` with the
`AMAZON_COOKIE_JAR_PATH` environment variable set to that site's cookie jar. A script that keeps sessions signed in can
get the path from `amazonorders_mcp.session.site_cookie_jar_path(config, site)`.

When your assistant changes the default site, the saved sessions move with it, so none is lost. If you change `domain`
with the CLI's `update-config` instead, sign in to the new default site again. Every other setting applies to every
site.

## Running in a Container

The server can also run in a container, e.g. on a home server, built from this repository's `Dockerfile`:

```sh
docker build -t amazon-orders-mcp .
```

The image runs the server over stdio, as a non-root user (UID 1000), and keeps its config and saved session in
`/home/app/.config/amazonorders`. Put that on a volume, which the server and the sign-in below share (a directory
mounted there instead must be writable by UID 1000):

```sh
docker run -i --rm -v amazon-orders:/home/app/.config/amazonorders amazon-orders-mcp
```

There's no display in a container, so `sign_in` can't open a browser window there. Instead, sign in with
`amazon-orders login`, in a one-off container on the same volume:

```sh
docker run -it --rm -v amazon-orders:/home/app/.config/amazonorders --entrypoint amazon-orders amazon-orders-mcp login
```

It asks for your username and password, and a one-time passcode if your account has two-step verification. The image
includes the Chromium that `amazon-orders` uses to clear Amazon's JavaScript challenges while signing in. `login` signs
out of a saved session first, and the server uses the new session on its next call, without a restart.

To sign in without being asked, e.g. from a script that signs in again when the session expires, give your credentials
to that one-off container with
[automated authentication](https://amazon-orders.readthedocs.io/#automating-authentication)
(`-e AMAZON_USERNAME -e AMAZON_PASSWORD -e AMAZON_OTP_SECRET_KEY`). Never give them to the server's container, which
only needs the saved session.

## Documentation

For more on the data the server returns, `amazon-orders`'s official documentation is available
at [Read the Docs](http://amazon-orders.readthedocs.io), including the
[Order](https://amazon-orders.readthedocs.io/api.html#amazonorders.entity.order.Order) entity's fields
and [troubleshooting](https://amazon-orders.readthedocs.io/troubleshooting.html).

## Contributing

Want to contribute financially? If you've found `amazon-orders-mcp`
useful, [sponsorship](https://github.com/sponsors/alexdlaird) would
be greatly appreciated!
