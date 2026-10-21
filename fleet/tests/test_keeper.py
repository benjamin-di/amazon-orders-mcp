import json
import subprocess

import pytest

from fleet.keeper import DAY, Credentials, Keeper, SignedOut, Settings, State, main, read_credentials


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Amazon:
    """Fake sites: which are signed in, what a sign-in does, and what was called."""

    def __init__(self, signed_in=(), login_works=True) -> None:
        self.signed_in = set(signed_in)
        self.login_works = login_works
        self.logins = []

    def probe(self, site: str) -> None:
        if site not in self.signed_in:
            raise SignedOut("no saved session")

    def login(self, site: str, credentials: Credentials) -> None:
        self.logins.append(site)
        if not self.login_works:
            raise RuntimeError("Amazon asked for input")
        self.signed_in.add(site)


CREDS = Credentials("user@example.invalid", "pw", "JBSWY3DPEHPK3PXP")


def keeper(tmp_path, amazon, clock, **overrides):
    settings = Settings(sites=["amazon.com", "amazon.de"], state_path=str(tmp_path / "keeper.json"),
                        probe_seconds=7200, max_logins=2, login_spacing_seconds=60, op_item="item",
                        op_vault="vault")
    settings = Settings(**{**settings.__dict__, **overrides})
    return Keeper(settings, amazon.probe, amazon.login, lambda: CREDS, clock=clock)


def test_signed_in_site_is_left_alone(tmp_path):
    amazon = Amazon(signed_in={"amazon.com"})
    assert keeper(tmp_path, amazon, Clock()).check("amazon.com") == "signed_in"
    assert amazon.logins == []


def test_signed_out_site_is_signed_in_again_and_recorded(tmp_path):
    amazon, clock = Amazon(), Clock()
    k = keeper(tmp_path, amazon, clock)
    assert k.check("amazon.de") == "signed_in_again"
    assert amazon.logins == ["amazon.de"]
    entry = State(str(tmp_path / "keeper.json")).read()["sites"]["amazon.de"]
    assert entry["logins"] == [clock.now] and entry["last_result"] == "signed_in_again"


def test_failed_sign_in_holds_until_cleared(tmp_path):
    amazon, clock = Amazon(login_works=False), Clock()
    k = keeper(tmp_path, amazon, clock)
    assert k.check("amazon.de") == "sign_in_failed_held"
    clock.now += DAY * 2  # a hold doesn't expire by itself
    assert k.check("amazon.de") == "signed_out_held"
    assert amazon.logins == ["amazon.de"]
    k.clear("amazon.de")
    amazon.login_works = True
    assert k.check("amazon.de") == "signed_in_again"


def test_budget_limits_sign_ins_per_day(tmp_path):
    amazon, clock = Amazon(), Clock()
    k = keeper(tmp_path, amazon, clock)
    for _ in range(2):
        assert k.check("amazon.de") == "signed_in_again"
        amazon.signed_in.clear()  # Amazon drops it again
        clock.now += 3600
    assert k.check("amazon.de") == "signed_out_budget"
    clock.now += DAY
    assert k.check("amazon.de") == "signed_in_again"


def test_sign_ins_are_spaced_across_sites(tmp_path):
    amazon, clock = Amazon(), Clock()
    k = keeper(tmp_path, amazon, clock)
    assert k.check("amazon.com") == "signed_in_again"
    clock.now += 10
    assert k.check("amazon.de") == "signed_out_spacing"
    clock.now += 60
    assert k.check("amazon.de") == "signed_in_again"


def test_probe_error_never_signs_in(tmp_path):
    amazon, clock = Amazon(), Clock()

    def broken(site):
        raise ConnectionError("Amazon 503")

    k = Keeper(keeper(tmp_path, amazon, clock).settings, broken, amazon.login, lambda: CREDS, clock=clock)
    assert k.check("amazon.com") == "probe_error:ConnectionError"
    assert amazon.logins == []


def test_credentials_never_printed():
    assert "pw" not in repr(CREDS) and "JBSWY" not in repr(CREDS)


def _op(stdout="", returncode=0, stderr=""):
    def run(args, **kwargs):
        assert args[:3] == ["op", "item", "get"] and "--reveal" in args
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)
    return run


def test_read_credentials_from_a_login_item():
    item = {"fields": [
        {"id": "username", "purpose": "USERNAME", "value": "user@example.invalid"},
        {"id": "password", "purpose": "PASSWORD", "value": "pw"},
        {"id": "TOTP_x", "type": "OTP", "value": "otpauth://totp/Amazon:user?secret=JBSWY3DPEHPK3PXP&issuer=Amazon"},
    ]}
    creds = read_credentials("item", "vault", run=_op(json.dumps(item)))
    assert creds == Credentials("user@example.invalid", "pw", "JBSWY3DPEHPK3PXP")


def test_read_credentials_refuses_an_incomplete_item():
    item = {"fields": [{"purpose": "USERNAME", "value": "u"}, {"purpose": "PASSWORD", "value": "p"}]}
    with pytest.raises(RuntimeError, match="one-time password"):
        read_credentials("item", "vault", run=_op(json.dumps(item)))


def test_read_credentials_reports_op_failure_without_values():
    with pytest.raises(RuntimeError, match="op item get failed"):
        read_credentials("item", "vault", run=_op(returncode=1, stderr="not found"))


def test_refuses_to_run_with_site_or_credential_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AMAZON_DOMAIN", "amazon.de")
    with pytest.raises(SystemExit):
        main(["check", "amazon.com"])


def test_clear_and_status_need_no_amazon(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AMAZON_KEEPER_STATE", str(tmp_path / "keeper.json"))
    with State(str(tmp_path / "keeper.json")).edit() as data:
        data["sites"]["amazon.es"] = {"logins": [], "held": {"since": 1, "reason": "x"}}
    assert main(["clear", "amazon.es"]) == 0
    assert "held" not in State(str(tmp_path / "keeper.json")).read()["sites"]["amazon.es"]
    capsys.readouterr()
    assert main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["sites"]["amazon.es"] == {"logins": []}


def test_sites_signed_out_together_are_all_signed_in_on_one_round(tmp_path):
    amazon, clock = Amazon(), Clock()
    k = keeper(tmp_path, amazon, clock)
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        clock.now += seconds

    assert k.check_all(sleep) == ["signed_in_again", "signed_in_again"]
    assert slept == [60]


def test_last_ok_survives_a_probe_error(tmp_path):
    amazon, clock = Amazon(signed_in={"amazon.com"}), Clock()
    k = keeper(tmp_path, amazon, clock)
    k.check("amazon.com")
    ok_at = clock.now

    def broken(site):
        raise ConnectionError("Amazon 503")

    clock.now += 7200
    Keeper(k.settings, broken, amazon.login, lambda: CREDS, clock=clock).check("amazon.com")
    entry = State(str(tmp_path / "keeper.json")).read()["sites"]["amazon.com"]
    assert entry["last_ok"] == ok_at and entry["last_result"] == "probe_error:ConnectionError"


def test_unreadable_credentials_hold_without_using_a_sign_in(tmp_path):
    amazon, clock = Amazon(), Clock()

    def broken():
        raise RuntimeError("op item get failed: couldn't start daemon")

    k = Keeper(keeper(tmp_path, amazon, clock).settings, amazon.probe, amazon.login, broken, clock=clock)
    assert k.check("amazon.de") == "credentials_failed_held"
    entry = State(str(tmp_path / "keeper.json")).read()["sites"]["amazon.de"]
    assert entry["logins"] == [] and entry["held"]["reason"].startswith("credentials: RuntimeError")
    assert amazon.logins == []
