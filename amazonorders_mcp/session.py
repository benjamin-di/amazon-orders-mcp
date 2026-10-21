__copyright__ = "Copyright (c) 2026 Alex Laird"
__license__ = "MIT"

import copy
import logging
import os
import subprocess
import sys
import threading
from contextlib import ExitStack
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar, Union
from urllib.parse import urlparse

from amazonorders.conf import AmazonOrdersConfig, cookies_file_lock
from amazonorders.contrib.browser.playwright import PlaywrightBrowserLogin
from amazonorders.exception import AmazonOrdersAuthError, AmazonOrdersBrowserNotInstalledError, AmazonOrdersError
from amazonorders.region_data import REGION_SITES
from amazonorders.session import AmazonSession, IODefault
from amazonorders.util import to_type

logger = logging.getLogger(__name__)

#: The command that signs in from a terminal, for a client that can't use the ``sign_in`` tool.
SIGN_IN_COMMAND = "amazon-orders login --browser"

#: What a sign-in reports while it waits for the user.
WAITING_TO_SIGN_IN_STATUS = "Waiting for the user to sign in to Amazon ..."

#: How many seconds the user has to sign in, kept short enough that a client's tool call doesn't time out first.
SIGN_IN_TIMEOUT = 120

#: The config keys that are the user's choice, changed only when they ask, and saved.
PREFERENCE_KEYS: Tuple[str, ...] = ("domain", "browser")

#: The config keys the assistant may adjust when calls keep failing, for this server run unless saved.
TUNING_KEYS: Tuple[str, ...] = ("request_timeout", "browser_timeout", "thread_pool_size", "connection_pool_size",
                                "max_auth_attempts", "max_auth_retries", "max_cookie_attempts",
                                "cookie_reattempt_wait", "auth_reattempt_wait", "bs4_parser",
                                "warn_on_missing_required_field")

#: The config keys that are a site's own, so an ``AMAZON_<KEY>`` environment variable for one sets the default
#: site's, and not another site's.
SITE_KEYS: Tuple[str, ...] = ("domain", "cookie_jar_path")

T = TypeVar("T")


class StderrIO(IODefault):
    """
    I/O for running over stdio, where ``stdout`` carries the MCP protocol and nobody is there to answer a prompt.
    Echoes go to ``stderr``, and a prompt raises instead of waiting for input.
    """

    def echo(self,
             msg: str,
             **kwargs: Any) -> None:
        print(msg, file=sys.stderr)

    def prompt(self,
               msg: str,
               type: Optional[Any] = None,
               **kwargs: Any) -> Any:
        raise AmazonOrdersAuthError(f"Amazon asked for input (\"{msg}\"), which the MCP server can't give.")


class SavedSession:
    """
    The Amazon sessions ``amazon-orders`` saved when the user signed in, shared by every tool call. A tool call only
    ever reuses one: no sign-in form is ever submitted, so credentials are never used and no browser is opened. If
    there's no saved session, or it has expired, an :class:`~amazonorders.exception.AmazonOrdersAuthError` is raised,
    and the user signs in again with :meth:`sign_in`, or with :data:`SIGN_IN_COMMAND` in a terminal.

    Each Amazon site, like amazon.com or amazon.de, has its own orders and its own sign-in, so each has its own
    session, made when it's first used. The default site is the config's ``domain``, and another site's session has
    the same config, but for that site, with its own cookie jar (see :func:`site_cookie_jar_path`).

    Calls to a site are serialized, since an :class:`~amazonorders.session.AmazonSession` isn't made for concurrent
    use, but calls to different sites can run at the same time, since their sessions share nothing. After Amazon
    rejects a site's session, it's loaded again from disk, so a sign-in saved meanwhile (e.g. by the CLI's ``login``)
    is used, and the rejected call is retried once with it.

    :param config: The config to use, by default the same one the ``amazon-orders`` CLI uses.
    """

    def __init__(self,
                 config: Optional[AmazonOrdersConfig] = None) -> None:
        self.config: Optional[AmazonOrdersConfig] = config
        self._site_sessions: Dict[str, _SiteSession] = {}
        self._lock = threading.Lock()

    def run(self,
            call: Callable[[AmazonSession], T],
            site: Optional[str] = None) -> T:
        """
        Run a call with a site's signed-in session.

        :param call: The call to make with the signed-in session.
        :param site: The Amazon site (e.g. ``amazon.de``), by default the config's ``domain``.
        :return: The call's result.
        :raises AmazonOrdersAuthError: If there's no saved session for the site, or it has expired.
        :raises AmazonOrdersError: If the site isn't an Amazon site that can be read.
        """
        site_session = self._site_session(site)
        with site_session.lock:
            return site_session.run(call)

    def sign_in(self,
                timeout: int = SIGN_IN_TIMEOUT,
                browser: Optional[str] = None,
                site: Optional[str] = None) -> None:
        """
        Open a browser window for the user to sign in on Amazon's own page, then save the session. Credentials are
        never seen, and other calls to the site wait until the sign-in is done.

        :param timeout: The number of seconds the user has to sign in.
        :param browser: The browser to sign in with, which is saved to the config so it's only chosen once. A
            Playwright browser type that isn't installed yet is downloaded first. By default, an installed Chrome,
            then Edge, or a Chromium that's been downloaded.
        :param site: The Amazon site to sign in to (e.g. ``amazon.de``), by default the config's ``domain``.
        :raises AmazonOrdersBrowserNotInstalledError: If no browser is installed to sign in with.
        :raises AmazonOrdersError: If a browser can't be downloaded, or the sign-in isn't completed in time, or Amazon
            doesn't accept it.
        """
        site_session = self._site_session(site)
        site_session.sign_in_status = WAITING_TO_SIGN_IN_STATUS
        if browser and browser != self.get_config()["preferences"]["browser"]:
            self.update_config("browser", browser)

        with site_session.lock:
            site_session.sign_in(timeout, browser)

    def sign_in_status(self,
                       site: str) -> str:
        """
        Get what an ongoing :meth:`sign_in` is doing, for the client to show while it waits. It never waits for a
        lock, so it can be called from the server's event loop.

        :param site: The Amazon site being signed in to, named as :meth:`site` names it.
        :return: The sign-in's status.
        """
        site_session = self._site_sessions.get(site)

        return site_session.sign_in_status if site_session else WAITING_TO_SIGN_IN_STATUS

    def get_config(self) -> Dict[str, Dict[str, Any]]:
        """
        Get the current value of each config key in :data:`PREFERENCE_KEYS` and :data:`TUNING_KEYS`.

        :return: The values, by key, under ``preferences`` and ``tuning``.
        """
        with self._lock:
            config = self._config()

            return {"preferences": {key: getattr(config, key) for key in PREFERENCE_KEYS},
                    "tuning": {key: getattr(config, key) for key in TUNING_KEYS}}

    def update_config(self,
                      key: str,
                      value: Union[str, int, float, bool, None],
                      save: Optional[bool] = None) -> Tuple[Any, bool]:
        """
        Change a config key in :data:`PREFERENCE_KEYS` or :data:`TUNING_KEYS`, for every site's session. A saved
        change is made with the ``amazon-orders`` CLI's ``update-config`` command, so the CLI follows it. A ``domain``
        must be an Amazon site ``amazon-orders`` knows, since the saved session's cookies are sent to it.

        A ``domain`` is the default site, whose session is kept in the config's ``cookie_jar_path``, so a change is
        always saved, and the saved sessions are moved with it: the old default site's to its own cookie jar, and
        the new default site's, if it has one, to ``cookie_jar_path`` (see :func:`site_cookie_jar_path`).

        :param key: The config key.
        :param value: The new value, read as the CLI reads it.
        :param save: Whether to save the change, or only make it for this server run. By default, a preference is
            saved and tuning isn't.
        :return: The value now in effect, and whether it was saved.
        :raises AmazonOrdersError: If the key can't be changed, or the value isn't valid for it.
        """
        if key not in PREFERENCE_KEYS + TUNING_KEYS:
            raise AmazonOrdersError(f"\"{key}\" isn't a setting that can be changed. Settings that can: "
                                    f"{', '.join(PREFERENCE_KEYS + TUNING_KEYS)}.")

        text = "" if value is None else str(value)
        if key == "domain":
            if save is False:
                raise AmazonOrdersError("The domain setting is the default site, so a change is always saved. To "
                                        "read another site without changing it, pass that site to the tool.")
            text = _to_amazon_domain(text)

        with self._lock, ExitStack() as site_locks:
            for site_session in self._site_sessions.values():
                site_locks.enter_context(site_session.lock)

            config = self._config()
            try:
                if key == "domain":
                    self._save_config(key, text)
                    saved = True
                    default_site = _default_site(config)
                    config.set_domain(text)
                    _move_saved_sessions(config, default_site, _default_site(config))
                else:
                    config.update_config(key, to_type(text), save=False)
                    saved = key in PREFERENCE_KEYS if save is None else save
                    if saved:
                        self._save_config(key, text)
            finally:
                for site_session in self._site_sessions.values():
                    site_session.reset()

            return getattr(config, key), saved

    def site(self,
             site: Optional[str] = None) -> str:
        """
        Get an Amazon site's name, the way sites are named here.

        :param site: The Amazon site (e.g. ``amazon.de``, or ``https://www.amazon.de``), by default (or if empty)
            the config's ``domain``.
        :return: The site's name (e.g. ``amazon.de``).
        :raises AmazonOrdersError: If the site isn't an Amazon site that can be read.
        """
        with self._lock:
            default_site = _default_site(self._config())

        return default_site if not site or site == default_site else _to_amazon_domain(site)

    def sites(self) -> List[str]:
        """
        Get the default site, then every other Amazon site that has a saved session (a cookie jar), signed in or not.

        :return: The sites' names.
        """
        with self._lock:
            config = self._config()
            default_site = _default_site(config)

            return [default_site] + [site for site in sorted(f"amazon.{suffix}" for suffix in REGION_SITES)
                                     if site != default_site and os.path.exists(site_cookie_jar_path(config, site))]

    def cookie_jar_path(self,
                        site: Optional[str] = None) -> str:
        """
        Get the cookie jar a site's session is kept in, as :func:`site_cookie_jar_path` does for this config.

        :param site: The Amazon site, by default the config's ``domain``.
        :return: The cookie jar's path.
        """
        with self._lock:
            config = self._config()

            return site_cookie_jar_path(config, site or _default_site(config))

    def base_url(self,
                 site: Optional[str] = None) -> str:
        """
        Get the Amazon site a session is for.

        :param site: The Amazon site, by default the config's ``domain``.
        :return: The Amazon site's base URL.
        """
        site_session = self._site_session(site)
        with site_session.lock:
            return site_session.config.constants.BASE_URL

    def _config(self) -> AmazonOrdersConfig:
        if self.config is None:
            self.config = AmazonOrdersConfig()

        return self.config

    def _site_session(self,
                      site: Optional[str]) -> "_SiteSession":
        name = self.site(site)
        with self._lock:
            self._config()
            if name not in self._site_sessions:
                self._site_sessions[name] = _SiteSession(lambda: self._site_config(name))

            return self._site_sessions[name]

    def _site_config(self,
                     site: str) -> AmazonOrdersConfig:
        """
        Build a site's config: the config itself for the default site, or else a copy of it for the other site, with
        the site's own cookie jar. Only called while the site's lock is held, so the config isn't being changed.
        """
        config = self._config()
        if site == _default_site(config):
            return config

        site_config = copy.deepcopy(config)
        # An environment variable outranks every other config source, so the copy drops the site's own keys from it
        for key in SITE_KEYS:
            site_config._environment.pop(key, None)
        site_config.update_config("cookie_jar_path", site_cookie_jar_path(config, site), save=False)
        site_config.set_domain(site)

        return site_config

    def _save_config(self,
                     key: str,
                     value: str) -> None:
        command = [sys.executable, "-m", "amazonorders.cli"]
        if self.config:
            command += ["--config-path", self.config.config_path]
        command += ["update-config", key, value]

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            logger.debug(f"Updating config \"{key}\" failed: {(result.stderr or result.stdout).strip()}")

            raise AmazonOrdersError(f"Could not change the {key} setting.")


class _SiteSession:
    """
    One Amazon site's saved session. Its lock is held for each of its calls, and while its config changes.

    :param new_config: Builds the site's config.
    """

    def __init__(self,
                 new_config: Callable[[], AmazonOrdersConfig]) -> None:
        self.lock = threading.Lock()
        #: What an ongoing :meth:`sign_in` is doing, for the client to show while it waits.
        self.sign_in_status: str = WAITING_TO_SIGN_IN_STATUS
        self._new_config = new_config
        self._config: Optional[AmazonOrdersConfig] = None
        self._amazon_session: Optional[AmazonSession] = None
        self._loaded_cookie_jar_mtime: Optional[float] = None

    @property
    def config(self) -> AmazonOrdersConfig:
        """
        The site's config, made when it's first needed.
        """
        if self._config is None:
            self._config = self._new_config()

        return self._config

    def reset(self) -> None:
        """
        Forget the site's config and session, after the config changed, so they're made again on the next call.
        """
        self._config = None
        self._amazon_session = None
        self._loaded_cookie_jar_mtime = None

    def run(self,
            call: Callable[[AmazonSession], T]) -> T:
        try:
            return self._run_signed_in(call)
        except AmazonOrdersAuthError:
            if not self._saved_session_changed():
                raise

            logger.debug("The saved session changed since it was loaded, so the call is retried with it")

            return self._run_signed_in(call)

    def sign_in(self,
                timeout: int,
                browser: Optional[str]) -> None:
        self.sign_in_status = WAITING_TO_SIGN_IN_STATUS
        amazon_session = self._session()

        browser_login = PlaywrightBrowserLogin(amazon_session)
        try:
            browser_login.login(timeout)
        except AmazonOrdersBrowserNotInstalledError as e:
            if not browser or (e.meta or {}).get("browser_types") != [browser]:
                raise

            self.sign_in_status = f"Downloading {browser} to sign in with ..."
            _download_browser(browser)

            self.sign_in_status = WAITING_TO_SIGN_IN_STATUS
            browser_login.login(timeout)
        finally:
            self._loaded_cookie_jar_mtime = amazon_session.cookie_jar_mtime

    def _run_signed_in(self,
                       call: Callable[[AmazonSession], T]) -> T:
        try:
            return call(self._signed_in())
        except AmazonOrdersAuthError:
            logger.debug("Amazon rejected the saved session, so it will be loaded again from disk on the next call")

            if self._amazon_session is not None:
                self._loaded_cookie_jar_mtime = self._amazon_session.cookie_jar_mtime
            self._amazon_session = None

            raise

    def _saved_session_changed(self) -> bool:
        return _cookie_jar_mtime(self.config) != self._loaded_cookie_jar_mtime

    def _signed_in(self) -> AmazonSession:
        amazon_session = self._session()
        if not amazon_session.is_authenticated:
            if not amazon_session.auth_cookies_stored():
                raise AmazonOrdersAuthError("There's no saved Amazon session.")

            logger.debug("Reusing the saved Amazon session ...")

            amazon_session.login()

        return amazon_session

    def _session(self) -> AmazonSession:
        if self._amazon_session is None:
            amazon_session = AmazonSession(io=StderrIO(), config=self.config)
            self._loaded_cookie_jar_mtime = amazon_session.cookie_jar_mtime
            self._amazon_session = self._reuse_only(amazon_session)

        return self._amazon_session

    @staticmethod
    def _reuse_only(amazon_session: AmazonSession) -> AmazonSession:
        amazon_session.auth_forms = []

        return amazon_session


def site_cookie_jar_path(config: AmazonOrdersConfig,
                         site: str) -> str:
    """
    Get the cookie jar an Amazon site's saved session is kept in, since each site has its own sign-in. The default
    site, the config's ``domain``, keeps its session in the config's ``cookie_jar_path``, where the ``amazon-orders``
    CLI keeps it. Every other site keeps its own next to it, named for the site: with the default ``cookies.json``,
    amazon.de's is ``cookies.amazon.de.json``.

    To save a session for a site other than the default from another process, run the CLI's ``login`` with
    ``--domain`` set to the site, and the ``AMAZON_COOKIE_JAR_PATH`` environment variable set to this path.

    :param config: The config, whose ``domain`` and ``cookie_jar_path`` are used.
    :param site: The Amazon site (e.g. ``amazon.de``, or ``https://www.amazon.de``).
    :return: The cookie jar's path.
    :raises AmazonOrdersError: If the site isn't an Amazon site that can be read.
    """
    default_site = _default_site(config)
    name = site if site == default_site else _to_amazon_domain(site)
    if name == default_site:
        return config.cookie_jar_path

    return _named_cookie_jar_path(config.cookie_jar_path, name)


def _named_cookie_jar_path(cookie_jar_path: str,
                           site: str) -> str:
    root, extension = os.path.splitext(cookie_jar_path)

    return f"{root}.{site}{extension}"


def _default_site(config: AmazonOrdersConfig) -> str:
    return (urlparse(config.constants.BASE_URL).hostname or "").removeprefix("www.")


def _move_saved_sessions(config: AmazonOrdersConfig,
                         old_default_site: str,
                         new_default_site: str) -> None:
    """
    Move the saved sessions with a change of the default site, so none is left behind: the old default site's from
    ``cookie_jar_path`` to its own cookie jar, then the new default site's from its own cookie jar to
    ``cookie_jar_path``.
    """
    if old_default_site == new_default_site:
        return

    with cookies_file_lock:
        moves = [(config.cookie_jar_path, _named_cookie_jar_path(config.cookie_jar_path, old_default_site)),
                 (_named_cookie_jar_path(config.cookie_jar_path, new_default_site), config.cookie_jar_path)]
        for source, destination in moves:
            if not os.path.exists(source):
                continue

            logger.debug(f"Moving the saved session in {source} to {destination} ...")

            try:
                os.replace(source, destination)
            except OSError as e:
                raise AmazonOrdersError(f"Could not move the saved session in {source} to {destination}: "
                                        f"{e}") from e


def _cookie_jar_mtime(config: AmazonOrdersConfig) -> Optional[float]:
    try:
        return os.path.getmtime(config.cookie_jar_path)
    except OSError:
        return None


def _download_browser(browser_type: str) -> None:
    """
    Download a Playwright browser type, as ``playwright install`` does. Its output is captured, because ``stdout``
    carries the MCP protocol and nothing else may be written to it.
    """
    result = subprocess.run([sys.executable, "-m", "playwright", "install", browser_type],
                            capture_output=True, text=True)
    if result.returncode != 0:
        detail = ((result.stderr or result.stdout).strip().splitlines() or [""])[-1]

        raise AmazonOrdersError(f"Could not download {browser_type}. {detail}".strip())


def _to_amazon_domain(site: str) -> str:
    value = site.strip().lower()
    host = (urlparse(value if "//" in value else f"//{value}").hostname or "").removeprefix("www.")
    suffix = host.removeprefix("amazon.") if host.startswith("amazon.") else ""
    if suffix not in REGION_SITES:
        supported = ", ".join(f"amazon.{suffix}" for suffix in sorted(REGION_SITES))

        raise AmazonOrdersError(f"\"{site}\" isn't an Amazon site that can be read. Supported sites: {supported}.")

    return f"amazon.{suffix}"
