import copy
import json
from pathlib import Path

from scripts.google_ads_launch_draft import validate_draft

ROOT = Path(__file__).resolve().parents[1]
DRAFT_PATH = ROOT / "config" / "google_ads_launch_draft.json"


def _draft():
    return json.loads(DRAFT_PATH.read_text())


def test_checked_in_google_ads_draft_is_valid_and_cannot_spend():
    draft = _draft()

    assert validate_draft(draft) == []
    assert draft["mode"] == "VALIDATE_ONLY"
    assert draft["campaign"]["status"] == "PAUSED"
    assert draft["activation_gate"]["approved"] is False


def test_rejects_any_serving_or_approval_state():
    draft = _draft()
    draft["campaign"]["status"] = "ENABLED"
    draft["activation_gate"]["approved"] = True
    draft["mode"] = "MUTATE"

    errors = validate_draft(draft)

    assert "mode must remain VALIDATE_ONLY" in errors
    assert "campaign.status must remain PAUSED" in errors
    assert "activation_gate.approved must remain false" in errors


def test_rejects_key_files_user_oauth_and_unmanaged_account_ids():
    draft = _draft()
    control_plane = draft["control_plane"]
    control_plane["authentication"] = "SERVICE_ACCOUNT_JSON_KEY"
    control_plane["persistent_service_account_key"] = True
    control_plane["customer_id_source"] = "OPERATOR_INPUT"

    errors = validate_draft(draft)

    assert "control_plane.authentication must equal APPLICATION_DEFAULT_CREDENTIALS" in errors
    assert "control_plane.persistent_service_account_key must remain false" in errors
    assert "control_plane.customer_id_source must equal SECRET_MANAGER" in errors


def test_rejects_budget_math_that_understates_google_charge_limits():
    draft = _draft()
    draft["campaign"]["budget"]["max_single_day_charge_usd"] = 20
    draft["campaign"]["budget"]["monthly_charge_limit_usd"] = 500

    errors = validate_draft(draft)

    assert "max_single_day_charge_usd must equal 2x average_daily_usd" in errors
    assert "monthly_charge_limit_usd must equal 30.4x average_daily_usd" in errors


def test_rejects_proportional_but_unreviewed_budget_and_bid_increases():
    draft = _draft()
    budget = draft["campaign"]["budget"]
    budget["average_daily_usd"] = 1000
    budget["max_single_day_charge_usd"] = 2000
    budget["monthly_charge_limit_usd"] = 30400
    draft["campaign"]["bidding"]["max_cpc_usd"] = 500

    errors = validate_draft(draft)

    assert "budget.average_daily_usd must equal reviewed value 20" in errors
    assert "bidding.max_cpc_usd must equal reviewed value 5" in errors


def test_rejects_housing_targeting_that_uses_zip_or_demographic_exclusions():
    draft = _draft()
    draft["campaign"]["geo"]["postal_codes"] = ["77336"]
    draft["campaign"]["housing_policy"]["age_enabled_all"] = False
    draft["campaign"]["housing_policy"]["audience_targeting"] = ["homeowners"]

    errors = validate_draft(draft)

    assert "postal-code targeting is prohibited for this housing campaign" in errors
    assert "all age groups must remain enabled" in errors
    assert "audience targeting must remain empty for the initial campaign" in errors


def test_rejects_broad_keywords_and_incomplete_responsive_search_ads():
    draft = _draft()
    ad_group = draft["campaign"]["ad_groups"][0]
    ad_group["keywords"][0]["match_type"] = "BROAD"
    ad_group["responsive_search_ad"]["headlines"] = ["Only One"]
    ad_group["responsive_search_ad"]["descriptions"] = ["Only one description"]

    errors = validate_draft(draft)

    assert any("only EXACT and PHRASE keywords are allowed" in error for error in errors)
    assert any("requires at least 3 headlines" in error for error in errors)
    assert any("requires at least 2 descriptions" in error for error in errors)


def test_rejects_noncanonical_landing_urls_and_missing_attribution():
    draft = _draft()
    ad_group = draft["campaign"]["ad_groups"][0]
    ad_group["responsive_search_ad"]["final_url"] = "https://example.com/inventory"
    draft["campaign"]["tracking"]["utm_medium"] = ""

    errors = validate_draft(draft)

    assert any("final_url must use https://www.texashomeoutlet.com" in error for error in errors)
    assert "tracking.utm_medium must equal cpc" in errors


def test_rejects_unreviewed_paths_and_unbounded_tracking_values():
    draft = _draft()
    ad_group = draft["campaign"]["ad_groups"][0]
    ad_group["responsive_search_ad"]["final_url"] = (
        "https://www.texashomeoutlet.com/crm?utm_source=raw"
    )
    draft["campaign"]["tracking"]["utm_term"] = "{searchterm}"

    errors = validate_draft(draft)

    assert any("final_url must use a reviewed public landing path" in error for error in errors)
    assert "tracking.utm_term must equal {keyword}" in errors


def test_rejects_inflated_stop_loss_or_removed_activation_checks():
    draft = _draft()
    draft["activation_gate"]["stop_loss"]["zero_reachable_leads_spend_usd"] = 50000
    draft["activation_gate"]["required_checks"] = []
    draft["campaign"]["geo"]["radius_miles"] = 500

    errors = validate_draft(draft)

    assert "stop_loss.zero_reachable_leads_spend_usd must equal reviewed value 200" in errors
    assert "activation_gate.required_checks must match the reviewed checklist" in errors
    assert "geo.radius_miles must equal reviewed value 50" in errors


def test_validation_does_not_mutate_the_launch_draft():
    draft = _draft()
    original = copy.deepcopy(draft)

    validate_draft(draft)

    assert draft == original
