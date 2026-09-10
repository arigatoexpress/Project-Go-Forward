"""Offline recovery preparation must not leak or create unusable PIN verifiers."""

import getpass
import importlib.util
import warnings
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def generator(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/generate_admin_pin_hash.py"
    spec = importlib.util.spec_from_file_location("pin_generator_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sys, "argv", [str(path)])
    return module


def test_rejects_argv_secret_without_echo_or_prompt(generator, monkeypatch, capsys):
    monkeypatch.setattr(generator.sys, "argv", ["generator", "synthetic-secret"])
    prompt = Mock(side_effect=AssertionError("must not prompt"))
    monkeypatch.setattr(generator.getpass, "getpass", prompt)
    assert generator.main() != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "synthetic-secret" not in output.err
    prompt.assert_not_called()


@pytest.mark.parametrize("pin", ["x" * 65, "\U0001f512" * 33])
def test_rejects_pin_longer_than_browser_utf16_limit(generator, monkeypatch, capsys, pin):
    monkeypatch.setattr(generator.getpass, "getpass", lambda _: pin)
    derive = Mock(return_value="should-not-be-generated")
    monkeypatch.setattr(generator, "make_hash", derive)
    assert generator.main() != 0
    assert capsys.readouterr().out == ""
    derive.assert_not_called()


def test_requires_matching_confirmation(generator, monkeypatch, capsys):
    answers = iter(["synthetic-one", "synthetic-two"])
    monkeypatch.setattr(generator.getpass, "getpass", lambda _: next(answers))
    assert generator.main() != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "synthetic-" not in output.err


def test_empty_input_does_not_generate_verifier(generator, monkeypatch, capsys):
    monkeypatch.setattr(generator.getpass, "getpass", Mock(return_value=""))
    assert generator.main() != 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "pin",
    [" synthetic-pin", "synthetic-pin ", "   ", "\u00a0synthetic-pin", "\ufeffsynthetic-pin\ufeff"],
)
def test_rejects_input_changed_by_browser_trim(generator, monkeypatch, capsys, pin):
    monkeypatch.setattr(generator.getpass, "getpass", Mock(return_value=pin))
    derive = Mock(return_value="should-not-be-generated")
    monkeypatch.setattr(generator, "make_hash", derive)
    assert generator.main() != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert pin not in output.err
    derive.assert_not_called()


def test_refuses_echoing_getpass_fallback(generator, monkeypatch, capsys):
    def fallback(_):
        warnings.warn("echo would be enabled", getpass.GetPassWarning)
        return "synthetic-pin"

    monkeypatch.setattr(generator.getpass, "getpass", fallback)
    assert generator.main() != 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_cancelled_input_exits_without_hash_or_traceback(generator, monkeypatch, capsys, error):
    monkeypatch.setattr(generator.getpass, "getpass", Mock(side_effect=error))
    assert generator.main() != 0
    output = capsys.readouterr()
    assert output.out == ""
    assert "Traceback" not in output.err


def test_refuses_verifier_on_interactive_terminal(generator, monkeypatch, capsys):
    monkeypatch.setattr(generator.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(generator.getpass, "getpass", Mock(return_value="synthetic-pin"))
    assert generator.main() != 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("pin", ["synthetic-pin", "synthetic pin", "x" * 64, "\U0001f512" * 32])
def test_confirmed_input_outputs_only_salted_verifier(generator, monkeypatch, capsys, pin):
    prompts = []

    def hidden_input(prompt):
        prompts.append(prompt)
        return pin

    monkeypatch.setattr(generator.getpass, "getpass", hidden_input)
    assert generator.main() == 0
    output = capsys.readouterr()
    assert output.out.startswith("scrypt$")
    assert output.out.count("\n") == 1
    assert pin not in output.out + output.err
    assert len(prompts) == 2
