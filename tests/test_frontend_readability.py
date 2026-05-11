from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_BROWSE = ROOT / "frontend/src/pages/InventoryBrowse.jsx"
DOCUMENT_CENTER = ROOT / "frontend/src/pages/DocumentCenter.jsx"
STATUS_BADGE = ROOT / "frontend/src/components/StatusBadge.jsx"
PROPERTY_CARD = ROOT / "frontend/src/components/PropertyCard.jsx"
IMAGE_RESEARCH = ROOT / "docs/MANUFACTURER_IMAGE_SOURCES_RESEARCH.md"


def test_inventory_hero_uses_readable_text_on_dark_photo_overlay():
    source = INVENTORY_BROWSE.read_text()

    assert "Inventory Refresh Live" in source
    assert "bg-black/45" in source
    assert "text-white/90" in source
    assert "text-white/80" in source
    assert (
        "text-[var(--cp-text-secondary)]"
        not in source[
            source.index("Inventory Refresh Live") : source.index("FeaturedHomeSpotlight")
        ]
    )


def test_floorplan_only_home_detail_opens_floorplan_instead_of_blank_photo_panel():
    source = INVENTORY_BROWSE.read_text()
    card_source = PROPERTY_CARD.read_text()

    assert "useState(() => photos.length === 0 && !!floorplan)" in source
    assert "setShowFloorplan(photos.length === 0 && !!floorplan)" in source
    assert "<Grid3X3 size={14} /> Floor Plan" in source
    assert "ref={modalRef}" in source
    assert "looksLikeBareModelFloorplan(url, filename)" in source
    assert "looksLikeBareModelFloorplan(url, filename)" in card_source


def test_home_status_badges_avoid_white_on_mid_tone_green_or_amber():
    source = STATUS_BADGE.read_text()

    assert "Available: 'bg-green-100 text-green-900 border border-green-300'" in source
    assert "'Pre-Owned': 'bg-amber-100 text-amber-950 border border-amber-300'" in source
    assert "Available: 'bg-green-500 text-white'" not in source
    assert "'Pre-Owned': 'bg-amber-500 text-white'" not in source


def test_document_center_inactive_navigation_remains_readable():
    source = DOCUMENT_CENTER.read_text()

    assert "'bg-white text-gray-600 border-gray-300'" in source
    assert "'border-gray-300 bg-gray-50 text-gray-600'" in source
    assert "text-xs font-medium text-gray-600" in source


def test_web_media_source_policy_blocks_unapproved_free_image_imports():
    source = IMAGE_RESEARCH.read_text()

    assert "2026-05-11 Web Search Addendum" in source
    assert "Publicly viewable images are not automatically reusable" in source
    assert "Generic free stock photos are also not acceptable as inventory photos" in source
    assert "Requires approval before production use" in source
