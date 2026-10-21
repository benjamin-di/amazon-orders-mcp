__copyright__ = "Copyright (c) 2026 Alex Laird"
__license__ = "MIT"

import io
import os
import threading
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from amazonorders.conf import AmazonOrdersConfig
from amazonorders.exception import AmazonOrdersAuthError, AmazonOrdersError

from amazonorders_mcp.session import SavedSession, StderrIO, site_cookie_jar_path
from tests.unittestcase import UnitTestCase


class TestSession(UnitTestCase):
    def test_saved_session_never_submits_sign_in_forms(self):
        # GIVEN
        self.given_saved_session()

        # WHEN
        amazon_session = self.saved_session.run(lambda signed_in_session: signed_in_session)

        # THEN
        self.assertEqual([], amazon_session.auth_forms)

    def test_echo_writes_to_stderr(self):
        # GIVEN
        stdout, stderr = io.StringIO(), io.StringIO()

        # WHEN
        with redirect_stdout(stdout), redirect_stderr(stderr):
            StderrIO().echo("Some message")

        # THEN
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("Some message\n", stderr.getvalue())

    def test_prompt_raises_instead_of_reading_input(self):
        # WHEN
        with self.assertRaises(AmazonOrdersAuthError) as cm:
            StderrIO().prompt("Enter OTP")

        # THEN
        self.assertIn("Enter OTP", str(cm.exception))

    def test_default_site_keeps_cookie_jar_path(self):
        # WHEN
        cookie_jar_paths = [site_cookie_jar_path(self.test_config, site)
                            for site in ("amazon.com", "https://www.amazon.com", self.saved_session.site())]

        # THEN
        self.assertEqual([self.test_cookie_jar_path] * 3, cookie_jar_paths)

    def test_other_site_has_cookie_jar_named_for_it(self):
        # WHEN
        cookie_jar_paths = [site_cookie_jar_path(self.test_config, site)
                            for site in ("amazon.de", "https://www.amazon.co.uk/", "Amazon.ES")]

        # THEN
        self.assertEqual([os.path.join(self.config_dir, "cookies.amazon.de.json"),
                          os.path.join(self.config_dir, "cookies.amazon.co.uk.json"),
                          os.path.join(self.config_dir, "cookies.amazon.es.json")], cookie_jar_paths)

    def test_cookie_jar_path_follows_configured_domain(self):
        # GIVEN
        self.test_config.set_domain("amazon.de")

        # WHEN
        default_cookie_jar_path = site_cookie_jar_path(self.test_config, "amazon.de")
        other_cookie_jar_path = site_cookie_jar_path(self.test_config, "amazon.com")

        # THEN
        self.assertEqual(self.test_cookie_jar_path, default_cookie_jar_path)
        self.assertEqual(os.path.join(self.config_dir, "cookies.amazon.com.json"), other_cookie_jar_path)

    def test_cookie_jar_path_of_unknown_site(self):
        # WHEN
        with self.assertRaises(AmazonOrdersError) as cm:
            site_cookie_jar_path(self.test_config, "ebay.com")

        # THEN
        self.assertIn("\"ebay.com\" isn't an Amazon site that can be read.", str(cm.exception))

    def test_each_site_has_its_own_session(self):
        # GIVEN
        self.given_saved_session("amazon.com", "amazon.de")

        # WHEN
        default_session = self.saved_session.run(lambda signed_in_session: signed_in_session)
        other_session = self.saved_session.run(lambda signed_in_session: signed_in_session, "www.amazon.de")

        # THEN
        self.assertIs(self.test_config, default_session.config)
        self.assertEqual("https://www.amazon.com", default_session.config.constants.BASE_URL)
        self.assertEqual("https://www.amazon.de", other_session.config.constants.BASE_URL)
        self.assertEqual(os.path.join(self.config_dir, "cookies.amazon.de.json"), other_session.config.cookie_jar_path)
        self.assertEqual(self.test_config.output_dir, other_session.config.output_dir)
        self.assertIs(other_session, self.saved_session.run(lambda signed_in_session: signed_in_session, "amazon.de"))

    def test_other_site_without_saved_session(self):
        # GIVEN
        self.given_saved_session()

        # WHEN
        with self.assertRaises(AmazonOrdersAuthError) as cm:
            self.saved_session.run(lambda signed_in_session: signed_in_session, "amazon.de")

        # THEN
        self.assertEqual("There's no saved Amazon session.", str(cm.exception))

    def test_unknown_site(self):
        # WHEN
        with self.assertRaises(AmazonOrdersError) as cm:
            self.saved_session.run(lambda signed_in_session: signed_in_session, "ebay.com")

        # THEN
        self.assertIn("\"ebay.com\" isn't an Amazon site that can be read. Supported sites: ", str(cm.exception))
        self.assertIn("amazon.co.uk, amazon.co.za, amazon.com", str(cm.exception))

    def test_environment_sets_only_default_site(self):
        # GIVEN
        environment_cookie_jar_path = os.path.join(self.config_dir, "jars", "cookies.json")
        with patch.dict(os.environ, {"AMAZON_DOMAIN": "amazon.com",
                                     "AMAZON_COOKIE_JAR_PATH": environment_cookie_jar_path}):
            self.saved_session = SavedSession(AmazonOrdersConfig(config_path=self.test_config.config_path))
        self.given_saved_session("amazon.de")

        # WHEN
        other_session = self.saved_session.run(lambda signed_in_session: signed_in_session, "amazon.de")

        # THEN
        self.assertEqual("https://www.amazon.de", other_session.config.constants.BASE_URL)
        self.assertEqual(os.path.join(self.config_dir, "jars", "cookies.amazon.de.json"),
                         other_session.config.cookie_jar_path)
        self.assertEqual("amazon.com", self.saved_session.config.domain)
        self.assertEqual(environment_cookie_jar_path, self.saved_session.config.cookie_jar_path)

    def test_sites_lists_default_and_every_saved_session(self):
        # GIVEN
        self.given_cookie_jar("amazon.es")
        self.given_cookie_jar("amazon.de")

        # WHEN
        sites = self.saved_session.sites()

        # THEN
        self.assertEqual(["amazon.com", "amazon.de", "amazon.es"], sites)

    def test_config_change_reaches_every_site(self):
        # GIVEN
        self.given_saved_session("amazon.com", "amazon.de")
        self.saved_session.run(lambda signed_in_session: signed_in_session, "amazon.de")

        # WHEN
        value, saved = self.saved_session.update_config("request_timeout", 30)
        other_session = self.saved_session.run(lambda signed_in_session: signed_in_session, "amazon.de")

        # THEN
        self.assertEqual((30, False), (value, saved))
        self.assertEqual(30, other_session.config.request_timeout)
        self.assertEqual("https://www.amazon.de", other_session.config.constants.BASE_URL)

    def test_changing_domain_moves_saved_sessions(self):
        # GIVEN
        self.test_config.save()
        self.given_saved_session("amazon.com", "amazon.de")
        with open(self.test_cookie_jar_path, encoding="utf-8") as f:
            com_cookie_jar = f.read()
        with open(site_cookie_jar_path(self.test_config, "amazon.de"), encoding="utf-8") as f:
            de_cookie_jar = f.read()

        # WHEN
        value, saved = self.saved_session.update_config("domain", "https://www.amazon.de")

        # THEN
        self.assertEqual(("amazon.de", True), (value, saved))
        self.assertEqual("amazon.de", AmazonOrdersConfig(config_path=self.test_config.config_path).domain)
        self.assertEqual("amazon.de", self.saved_session.site())
        with open(self.test_cookie_jar_path, encoding="utf-8") as f:
            self.assertEqual(de_cookie_jar, f.read())
        with open(os.path.join(self.config_dir, "cookies.amazon.com.json"), encoding="utf-8") as f:
            self.assertEqual(com_cookie_jar, f.read())
        self.assertFalse(os.path.exists(os.path.join(self.config_dir, "cookies.amazon.de.json")))
        self.assertEqual("https://www.amazon.de",
                         self.saved_session.run(lambda signed_in_session: signed_in_session).config.constants.BASE_URL)
        self.assertEqual(["amazon.de", "amazon.com"], self.saved_session.sites())

    def test_changing_domain_without_saving(self):
        # WHEN
        with self.assertRaises(AmazonOrdersError) as cm:
            self.saved_session.update_config("domain", "amazon.de", save=False)

        # THEN
        self.assertIn("always saved", str(cm.exception))
        self.assertEqual("amazon.com", self.saved_session.site())

    def test_sites_are_read_at_the_same_time(self):
        # GIVEN
        self.given_saved_session("amazon.com", "amazon.de")
        default_call_started, other_call_done = threading.Event(), threading.Event()

        def default_call(signed_in_session):
            default_call_started.set()
            return other_call_done.wait(5)

        default_thread_result = []
        default_thread = threading.Thread(
            target=lambda: default_thread_result.append(self.saved_session.run(default_call)))
        default_thread.start()
        default_call_started.wait(5)

        # WHEN
        self.saved_session.run(lambda signed_in_session: other_call_done.set(), "amazon.de")
        default_thread.join(5)

        # THEN
        self.assertEqual([True], default_thread_result)
