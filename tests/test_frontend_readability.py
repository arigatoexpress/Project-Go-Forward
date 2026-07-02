from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend/src/App.jsx"
INVENTORY_BROWSE = ROOT / "frontend/src/pages/InventoryBrowse.jsx"
DOCUMENT_CENTER = ROOT / "frontend/src/pages/DocumentCenter.jsx"
AD_STUDIO = ROOT / "frontend/src/pages/AdStudio.jsx"
SYSTEM_HUB = ROOT / "frontend/src/pages/SystemHub.jsx"
STATUS_BADGE = ROOT / "frontend/src/components/StatusBadge.jsx"
PROPERTY_CARD = ROOT / "frontend/src/components/PropertyCard.jsx"
ABOUT_PAGE = ROOT / "frontend/src/pages/About.jsx"
FINANCING_PAGE = ROOT / "frontend/src/pages/Financing.jsx"
FAQ_PAGE = ROOT / "frontend/src/pages/FAQ.jsx"
WARRANTY_PAGE = ROOT / "frontend/src/pages/Warranty.jsx"
DELIVERY_PAGE = ROOT / "frontend/src/pages/Delivery.jsx"
CONTENT_PAGE = ROOT / "frontend/src/components/ContentPage.jsx"
IMAGE_RESEARCH = ROOT / "docs/MANUFACTURER_IMAGE_SOURCES_RESEARCH.md"


def test_inventory_hero_uses_readable_text_on_dark_photo_overlay():
    source = INVENTORY_BROWSE.read_text()

    assert "Live Inventory + Orderable Floorplans" in source
    assert "Mobile Homes For Sale in Huffman, TX" in source
    assert "bg-black/45" in source
    assert "text-white/90" in source
    assert "text-white/80" in source
    assert (
        "text-[var(--cp-text-secondary)]"
        not in source[
            source.index("Live Inventory + Orderable Floorplans") : source.index(
                "FeaturedHomeSpotlight"
            )
        ]
    )


def test_inventory_cards_keep_legacy_quote_routes_visible():
    source = INVENTORY_BROWSE.read_text()

    assert "function getLegacyQuoteUrl(home)" in source
    assert "Price Quote" in source
    assert "quoteDetailMatch" in source


def test_inventory_cards_replace_slow_or_failed_cdn_images_with_visible_fallback():
    source = INVENTORY_BROWSE.read_text()

    assert "heroLoadState" in source
    assert "setHeroLoadState((state) => (state === 'loaded' ? state : 'failed'))" in source
    assert 'loading="lazy"' in source
    assert 'decoding="async"' in source
    assert "onError={() => setHeroLoadState('failed')}" in source
    assert "Photo Unavailable" in source


def test_floorplan_only_home_detail_opens_floorplan_instead_of_blank_photo_panel():
    source = INVENTORY_BROWSE.read_text()
    card_source = PROPERTY_CARD.read_text()

    assert "useState(() => photos.length === 0 && !!floorplan)" in source
    assert "setShowFloorplan(photos.length === 0 && !!floorplan)" in source
    assert "<Grid3X3 size={14} /> Floor Plan" in source
    assert "ref={modalRef}" in source
    assert "looksLikeBareModelFloorplan(url, filename)" in source
    assert "looksLikeBareModelFloorplan(url, filename)" in card_source
    assert r"/[-_\s]\d+$/.test(stem)" in source
    assert r"/[-_\s]\d+$/.test(stem)" in card_source


def test_home_status_badges_avoid_white_on_mid_tone_green_or_amber():
    source = STATUS_BADGE.read_text()

    assert "Available: 'bg-green-100 text-green-900 border border-green-300'" in source
    assert "Orderable: 'bg-blue-100 text-blue-900 border border-blue-300'" in source
    assert "'Pre-Owned': 'bg-amber-100 text-amber-950 border border-amber-300'" in source
    assert "Available: 'bg-green-500 text-white'" not in source
    assert "'Pre-Owned': 'bg-amber-500 text-white'" not in source


def test_inventory_browse_exposes_orderable_floorplan_filter():
    source = INVENTORY_BROWSE.read_text()

    assert "function getAvailabilityKind(home)" in source
    assert "orderable_floorplan" in source
    # The orderable affordance is the FILTER chip + the "orderable floorplans
    # visible" subtext. (The hero stat card was relabeled "Houses" per client
    # feedback — on-lot lot models now surface as "Special Deals" — so the old
    # "Orderable Plans" hero label is intentionally gone.)
    assert "Orderable ({orderableCount})" in source
    assert "orderable floorplans visible" in source


def test_document_center_inactive_navigation_remains_readable():
    source = DOCUMENT_CENTER.read_text()

    assert "'bg-white text-gray-600 border-gray-300'" in source
    assert "'border-gray-300 bg-gray-50 text-gray-600'" in source
    assert "text-xs font-medium text-gray-600" in source


def test_admin_cookie_verification_closes_pin_overlay_on_admin_routes():
    source = APP.read_text()

    assert "if (adminAuthed)" in source
    assert "setShowPinModal(false)" in source
    assert "setPinError('')" in source


def test_web_media_source_policy_blocks_unapproved_free_image_imports():
    source = IMAGE_RESEARCH.read_text()

    assert "2026-05-11 Web Search Addendum" in source
    assert "Publicly viewable images are not automatically reusable" in source
    assert "Generic free stock photos are also not acceptable as inventory photos" in source
    assert "Requires approval before production use" in source


def test_ad_studio_surfaces_readiness_and_image_fallbacks():
    source = AD_STUDIO.read_text()

    assert "apiGetGcpReadiness" in source
    assert "apiGetSocialReadiness" in source
    assert "THO_SOCIAL_PUBLISH_ENABLED" in source
    assert "handleImgFallback" in source
    assert "selectedPhotoUrl" in source
    assert "video_url: generatedGenAIClip?.download_url || generatedVideo?.download_url" in source


def test_app_wires_all_trust_content_routes():
    source = APP.read_text()
    for route in ("'/about'", "'/financing'", "'/faq'", "'/warranty'", "'/delivery'"):
        assert route in source, f"missing route mapping for {route}"
    for page in ("About", "Financing", "FAQ", "Warranty", "Delivery"):
        assert f"const {page} = lazy(() => import('./pages/{page}'))" in source


def test_content_pages_share_consistent_layout():
    source = CONTENT_PAGE.read_text()
    assert "ContentPage" in source
    assert "onBack" in source
    assert "max-w-4xl" in source
    assert "bg-white" in source


def test_about_page_carries_business_identity():
    source = ABOUT_PAGE.read_text()
    assert "BUSINESS_NAME" in source
    assert "BUSINESS_LEGAL_NAME" in source
    assert "BUSINESS_LICENSE" in source
    assert "Who We Are" in source


def test_faq_page_has_visible_qa_pairs():
    source = FAQ_PAGE.read_text()
    assert "FAQ_ITEMS" in source
    assert "FAQPage" not in source  # schema lives server-side; page is visible Q&A
    assert "How does financing work for a manufactured home?" in source
    assert "Do I need land to buy a home from you?" in source


def test_financing_page_avoids_specific_rate_claims():
    source = FINANCING_PAGE.read_text()
    assert "Chattel" in source
    assert "FHA" in source
    assert "VA" in source
    assert "monthly payment" in source
    # The page must not invent specific rates or guarantees.
    assert "interest rate" not in source.lower()
    assert "%" not in source


def test_warranty_page_prioritizes_safety_escalation():
    source = WARRANTY_PAGE.read_text()
    assert "Safety First" in source
    assert "911" in source
    assert "carbon monoxide" in source


def test_delivery_page_lists_service_area():
    source = DELIVERY_PAGE.read_text()
    assert "Huffman" in source
    assert "Humble" in source
    assert "Baytown" in source
    assert "Site Prep" in source
