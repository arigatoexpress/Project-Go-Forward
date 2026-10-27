"""Coverage swarm c10-c: root_agent import-fallback branches + schemas/output_schemas.

Target 1 — root_agent.py lines 106-107, 143-144, 172-173:
    Each agent factory tries the absolute import ``from tools import (...)``
    first and falls back to the relative ``from .tools import (...)`` on
    ImportError. In the test process root_agent is imported as a top-level
    module (``__package__ == ''``), so the absolute import always wins and the
    fallback never runs. To exercise the fallback in-process we:

      1. Replace ``sys.modules['tools']`` with a stub whose module-level
         ``__getattr__`` raises ImportError for every tool name, so the
         absolute ``from tools import ...`` fails.
      2. Inject a fake parent package (``_FAKE_PKG``) into ``sys.modules``
         whose ``.tools`` entry is the *real* tools package.
      3. Execute root_agent.py's source in a throwaway module object whose
         ``__package__`` is the fake package, so the relative
         ``from .tools import ...`` resolves to the real tools module and all
         three fallback branches run (coverage is tracked per file, so these
         lines count). The real ``root_agent`` module is never touched.
      4. Restore ``sys.modules`` in a try/finally.

    Note: ``importlib.reload`` cannot be used here — reload resets
    ``__package__`` from the module's spec before exec, so a pre-set fake
    package is wiped before the relative import runs.

    A subprocess test doubles as behavioral verification that the fallback
    actually produces a working agent (subprocess runs do not earn in-process
    coverage credit; the exec test above does).

Target 2 — schemas/output_schemas.py (0% covered, never imported by any test):
    Pure pydantic models with no validators; importing the module covers the
    class/Field definitions, and each model is exercised with valid
    construction plus ValidationError branches for missing required fields
    and bad types.
"""

import importlib.util
import subprocess
import sys
import types as pytypes
from pathlib import Path

import pytest
from pydantic import ValidationError

# root_agent hard-requires google-adk; the fallback tests below execute its
# source, so skip them in dependency-minimal environments.
_HAS_GOOGLE_ADK = importlib.util.find_spec("google.adk") is not None
requires_google_adk = pytest.mark.skipif(
    not _HAS_GOOGLE_ADK, reason="google-adk is not installed"
)

# Ensure repo root is importable (same pattern as tests/test_agent_booking_fix.py)
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_FAKE_PKG = "pgf_c10c_fakepkg"


def _make_blocked_tools_stub() -> pytypes.ModuleType:
    """A fake ``tools`` module whose attribute access always raises ImportError."""
    stub = pytypes.ModuleType("tools")

    def _blocked(name: str):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)  # let import machinery probe dunders normally
        raise ImportError(f"tools.{name} blocked by coverage test")

    stub.__getattr__ = _blocked  # PEP 562 module-level getattr
    return stub


def _exec_root_agent_under_fake_package() -> pytypes.ModuleType:
    """Run root_agent.py's source with a fake ``__package__`` so the relative-import fallback fires.

    Returns the throwaway module; the real ``root_agent`` in sys.modules is untouched.
    """
    source_path = REPO_ROOT / "root_agent.py"
    module = pytypes.ModuleType(f"{_FAKE_PKG}.root_agent")
    module.__package__ = _FAKE_PKG
    module.__file__ = str(source_path)
    sys.modules[module.__name__] = module
    code = compile(source_path.read_text(), str(source_path), "exec")
    exec(code, module.__dict__)  # noqa: S102 - deliberate controlled re-execution
    return module


class TestRootAgentImportFallback:
    """In-process coverage of the except-ImportError relative-import fallbacks."""

    @requires_google_adk
    def test_fallback_branches_execute_on_reload(self):
        import root_agent  # noqa: F401 - ensures the real module is imported first
        import tools as real_tools

        saved_tools = sys.modules.get("tools")
        fake_pkg = pytypes.ModuleType(_FAKE_PKG)
        fake_pkg.__path__ = [str(REPO_ROOT)]

        try:
            sys.modules["tools"] = _make_blocked_tools_stub()
            sys.modules[_FAKE_PKG] = fake_pkg
            sys.modules[f"{_FAKE_PKG}.tools"] = real_tools

            # Absolute `from tools import ...` now raises ImportError for every
            # name; the relative `from .tools import ...` resolves via the fake
            # package to the real tools module. Executing the module source
            # runs all three factories, covering lines 106-107, 143-144, 172-173.
            reloaded = _exec_root_agent_under_fake_package()

            # Behavioral sanity: the throwaway module built a fully working
            # agent through the relative-import path.
            assert reloaded.root_agent.name == "root_agent"
            sales = reloaded._create_sales_agent()
            service = reloaded._create_service_agent()
            assert {t.__name__ for t in sales.tools} == {
                "search_inventory",
                "get_current_datetime",
                "check_available_slots",
                "get_business_hours",
                "save_lead",
            }
            assert {t.__name__ for t in service.tools} == {
                "check_warranty_status",
                "analyze_defect_image",
                "generate_work_order_pdf",
                "generate_service_ticket",
                "generate_customer_email",
            }
        finally:
            if saved_tools is not None:
                sys.modules["tools"] = saved_tools
            else:
                sys.modules.pop("tools", None)
            sys.modules.pop(_FAKE_PKG, None)
            sys.modules.pop(f"{_FAKE_PKG}.tools", None)
            sys.modules.pop(f"{_FAKE_PKG}.root_agent", None)

    @requires_google_adk
    def test_planner_kwargs_branch(self, monkeypatch):
        """Line 187: kwargs['planner'] is set only when _build_planner() is truthy."""
        from google.adk.planners import BuiltInPlanner
        from google.genai import types

        import root_agent

        planner = BuiltInPlanner(
            thinking_config=types.ThinkingConfig(include_thoughts=False, thinking_budget=128)
        )
        monkeypatch.setattr(root_agent, "_build_planner", lambda: planner)
        agent = root_agent._create_root_agent()
        assert agent.planner is planner


_FALLBACK_SUBPROCESS = r"""
import sys
import types
from pathlib import Path

repo_root = Path(%r)
sys.path.insert(0, str(repo_root))

pkg = "pgf_c10c_subpkg"
import tools as real_tools

stub = types.ModuleType("tools")
def _blocked(name):
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    raise ImportError("blocked: " + name)
stub.__getattr__ = _blocked
fake_pkg = types.ModuleType(pkg)
fake_pkg.__path__ = [str(repo_root)]
sys.modules["tools"] = stub
sys.modules[pkg] = fake_pkg
sys.modules[pkg + ".tools"] = real_tools

# Execute root_agent.py's source under the fake package: the absolute
# `from tools import ...` hits the blocked stub, the relative
# `from .tools import ...` resolves to the real tools package.
source_path = repo_root / "root_agent.py"
mod = types.ModuleType(pkg + ".root_agent")
mod.__package__ = pkg
mod.__file__ = str(source_path)
exec(compile(source_path.read_text(), str(source_path), "exec"), mod.__dict__)

agent = mod._create_root_agent()
assert agent.name == "root_agent"
assert [t.__name__ for t in agent.tools] == ["get_business_hours"]
assert [a.name for a in agent.sub_agents] == ["sales_agent", "service_agent"]
assert mod.root_agent.name == "root_agent"
print("FALLBACK_OK")
"""


@requires_google_adk
def test_import_fallback_behavior_in_subprocess():
    """Behavioral check that the relative-import fallback yields a working agent."""
    script = _FALLBACK_SUBPROCESS % str(REPO_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert "FALLBACK_OK" in result.stdout


# ---------------------------------------------------------------------------
# Target 2 — schemas/output_schemas.py
# ---------------------------------------------------------------------------


class TestOutputSchemas:
    """Exercise every pydantic model in schemas/output_schemas.py."""

    def test_product_search_result_defaults(self):
        from schemas.output_schemas import ProductSearchResult

        p = ProductSearchResult(
            id="p-1",
            model_name="WH-100",
            manufacturer="Acme",
            classification="range",
            specs={"fuel": "gas"},
            pricing={"display_price": "$999"},
        )
        assert p.image_url is None
        assert p.gallery_images == []
        assert p.detail_url is None
        assert p.match_score is None

    def test_product_search_result_missing_required(self):
        from schemas.output_schemas import ProductSearchResult

        with pytest.raises(ValidationError):
            ProductSearchResult()  # all required fields missing

    def test_search_response_round_trip(self):
        from schemas.output_schemas import ProductSearchResult, SearchResponse

        product = ProductSearchResult(
            id="p-2",
            model_name="DW-200",
            manufacturer="Acme",
            classification="dishwasher",
            specs={},
            pricing={},
        )
        resp = SearchResponse(
            query_summary="gas ranges under $1000",
            total_matches=1,
            results=[product],
        )
        assert resp.suggestions == []
        rebuilt = SearchResponse.model_validate(resp.model_dump())
        assert rebuilt == resp

    def test_search_response_bad_types(self):
        from schemas.output_schemas import SearchResponse

        with pytest.raises(ValidationError):
            SearchResponse(
                query_summary="x",
                total_matches="not-a-number",
                results="not-a-list",
            )

    def test_search_response_missing_required(self):
        from schemas.output_schemas import SearchResponse

        with pytest.raises(ValidationError):
            SearchResponse()

    def test_lead_info_defaults(self):
        from schemas.output_schemas import LeadInfo

        lead = LeadInfo(interest="washers")
        assert lead.name is None
        assert lead.email is None
        assert lead.phone is None
        assert lead.budget_range is None
        assert lead.preferred_specs == {}
        assert lead.urgency == "medium"
        assert lead.notes == ""

    def test_lead_info_missing_required(self):
        from schemas.output_schemas import LeadInfo

        with pytest.raises(ValidationError):
            LeadInfo()

    def test_appointment_request_defaults(self):
        from schemas.output_schemas import AppointmentRequest

        appt = AppointmentRequest(
            customer_name="Ari",
            contact_method="ari@example.com",
            purpose="showroom visit",
        )
        assert appt.preferred_date is None
        assert appt.preferred_time is None
        assert appt.products_of_interest == []

    def test_appointment_request_missing_required(self):
        from schemas.output_schemas import AppointmentRequest

        with pytest.raises(ValidationError):
            AppointmentRequest(customer_name="Ari")  # contact_method, purpose missing

    def test_service_request_defaults(self):
        from schemas.output_schemas import ServiceRequest

        req = ServiceRequest(
            customer_name="Ari",
            product_id="SN-123",
            issue_description="burner won't light",
            issue_category="repair",
        )
        assert req.purchase_date is None
        assert req.severity == "medium"
        assert req.photos_provided is False

    def test_service_request_missing_required(self):
        from schemas.output_schemas import ServiceRequest

        with pytest.raises(ValidationError):
            ServiceRequest(customer_name="Ari")  # product_id, issue_* missing

    def test_routing_decision_valid_and_missing(self):
        from schemas.output_schemas import RoutingDecision

        decision = RoutingDecision(
            target_agent="sales_agent",
            confidence=0.9,
            reasoning="customer wants pricing",
            customer_intent="buy a range",
        )
        assert decision.confidence == 0.9

        with pytest.raises(ValidationError):
            RoutingDecision(
                target_agent="sales_agent",
                confidence="high",  # must be a float
                reasoning="x",
                customer_intent="y",
            )
        with pytest.raises(ValidationError):
            RoutingDecision(target_agent="sales_agent")  # three fields missing
