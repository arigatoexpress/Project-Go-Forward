from bs4 import BeautifulSoup

from tools.inventory_sync import InventorySync

CARD_HTML = """
<div class="fp-card-container">
  <div class="fp-card-image lazyload"
       data-bg="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/28102/NEW%20YEAR%20CLEARANCE%20SALE_card_lg.jpg">
    <div class="fp-card-tags">
      <span class="label">Manufactured</span>
      <span class="label">Lot Model</span>
      <span class="label">New</span>
      <span class="label">Clearance</span>
    </div>
    <a class="fp-thumb fancybox-print-link"
       href="https://d132mt2yijm03y.cloudfront.net/manufacturer/2007/floorplan/222250/DELIGHT.jpg"
       title="Floor Plan"></a>
    <a class="tour-thumb"
       href="https://my.matterport.com/show/?m=SvVRKXdXUQq&amp;play=1"
       title="3D Tour"></a>
  </div>
  <div class="fp-card-details">
    <h4 class="home-name">
      <a href="/inventory-detail/28102/texas-home-outlet/huffman/new-year-clearance-sale/">
        NEW YEAR CLEARANCE SALE / TRU Single Section Delight
      </a>
    </h4>
    <div class="fp-card-built-by">Built by: TRU</div>
    <div class="row fp-card-specs">
      <div><i class="icon-mfh-bed"></i>2</div>
      <div><i class="icon-mfh-bath"></i>2.00</div>
      <div><i class="icon-mfh-tapemeasure"></i>820 ft²</div>
      <div><i class="icon-mfh-lengthwidth"></i>14'0" x 60'0"</div>
    </div>
  </div>
</div>
"""


def test_page_one_uses_canonical_inventory_url():
    sync = InventorySync(dry_run=True)

    assert sync._inventory_page_url(1) == "https://www.texashomeoutlet.com/inventory/"
    assert sync._inventory_page_url(2) == "https://www.texashomeoutlet.com/inventory/?pg=2"


def test_default_firestore_project_points_at_current_tho_project(monkeypatch):
    sync = InventorySync(dry_run=True)
    monkeypatch.delenv("FIRESTORE_PROJECT_ID", raising=False)

    assert sync._firestore_project_id() == "tho-ai-agent"


def test_model_key_normalization_matches_legacy_titles_to_existing_records():
    sync = InventorySync(dry_run=True)

    assert sync._normalize_model_key("The Promotional Series / The Nassau FAC28483A") == "nassau"
    assert sync._normalize_model_key("The Nassau") == "nassau"
    assert sync._normalize_model_key("PRE-OWNED / Big Blue") == "big blue"
    assert sync._normalize_model_key("Big Blue") == "big blue"


def test_extract_card_data_reads_floorplan_dimensions_tags_and_matterport():
    sync = InventorySync(dry_run=True)
    card = BeautifulSoup(CARD_HTML, "html.parser").select_one(".fp-card-container")

    home = sync._extract_card_data(card)

    assert home["id"] == "28102"
    assert home["model_name"] == "NEW YEAR CLEARANCE SALE / TRU Single Section Delight"
    assert home["manufacturer"] == "TRU"
    assert home["specs"] == {"beds": 2, "baths": 2.0, "sq_ft": 820, "width": 14, "length": 60}
    assert home["floorplan_url"].endswith("/DELIGHT.jpg")
    assert home["matterport_id"] == "SvVRKXdXUQq"
    assert home["tags"] == ["Manufactured", "Lot Model", "New", "Clearance"]
    assert home["is_new"] is True


def test_transform_preserves_floorplan_tour_dimensions_and_location():
    sync = InventorySync(dry_run=True)
    card = BeautifulSoup(CARD_HTML, "html.parser").select_one(".fp-card-container")
    raw_home = sync._extract_card_data(card)

    item = sync.transform_to_inventory_item(raw_home)
    data = item.to_firestore_dict()

    assert data["model_name"] == "NEW YEAR CLEARANCE SALE / TRU Single Section Delight"
    assert data["manufacturer"] == "TRU Homes"
    assert data["width"] == 14
    assert data["length"] == 60
    assert data["sections"] == 1
    assert data["classification"] == "Single Wide"
    assert data["floorplan_url"].endswith("/DELIGHT.jpg")
    assert data["matterport_id"] == "SvVRKXdXUQq"
    assert data["tags"] == ["Manufactured", "Lot Model", "New", "Clearance"]
    assert data["location"] == "Huffman, TX"
