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


# ---------------------------------------------------------------------------
# Remaining sync paths: scraping loop, Firestore lookup/sync, run orchestration
# ---------------------------------------------------------------------------


class FakeDocSnap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, collection, doc_id):
        self._collection = collection
        self.id = doc_id

    def get(self, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        if self._collection.raise_on == "get":
            raise RuntimeError("get failed")
        return FakeDocSnap(self.id, self._collection._docs.get(self.id))

    def set(self, data, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        if self._collection.raise_on == "set":
            raise RuntimeError("set failed")
        self._collection._docs[self.id] = dict(data)
        self._collection.ops.append(("set", self.id))

    def update(self, data, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        if self._collection.raise_on == "update":
            raise RuntimeError("update failed")
        self._collection._docs.setdefault(self.id, {}).update(data)
        self._collection.ops.append(("update", self.id))


class FakeCollection:
    def __init__(self, docs, raise_on=None):
        self._docs = docs
        self.ops = []
        self.raise_on = raise_on

    def document(self, doc_id):
        return FakeDocRef(self, doc_id)

    def limit(self, _count):
        return self

    def stream(self, timeout=None):
        assert timeout is not None, "Firestore RPCs must carry the bounded timeout"
        if self.raise_on == "stream":
            raise RuntimeError("stream failed")
        for doc_id, data in list(self._docs.items()):
            yield FakeDocSnap(doc_id, dict(data))


class FakeFirestore:
    def __init__(self, inventory_docs=None, raise_on=None):
        self._inventory = FakeCollection(inventory_docs or {}, raise_on=raise_on)

    def collection(self, name):
        assert name == "inventory"
        return self._inventory


def _sync_with_db(inventory_docs=None, raise_on=None):
    sync = InventorySync(dry_run=True)  # dry_run skips _init_firestore
    sync.db = FakeFirestore(inventory_docs, raise_on=raise_on)
    sync.dry_run = False  # exercise the real sync path against the fake db
    return sync


def _item(sync, model_name="The Delight", item_id="99"):
    return sync.transform_to_inventory_item(
        {
            "id": item_id,
            "model_name": model_name,
            "manufacturer": "TRU",
            "specs": {"beds": 2, "baths": 2.0, "sq_ft": 820, "width": 14, "length": 60},
            "price": 89900.0,
            "year": 2026,
            "is_new": True,
            "image_url": "https://example.com/hero.jpg",
            "floorplan_url": None,
            "matterport_id": None,
            "tags": ["New"],
            "detail_url": "https://www.texashomeoutlet.com/inventory-detail/99/x/",
        }
    )


class TestMatterportExtraction:
    def test_extracts_id_from_show_url(self):
        sync = InventorySync(dry_run=True)
        url = "https://my.matterport.com/show/?m=SvVRKXdXUQq&play=1"
        assert sync._extract_matterport_id(url) == "SvVRKXdXUQq"

    def test_missing_param_returns_none(self):
        sync = InventorySync(dry_run=True)
        assert sync._extract_matterport_id("https://my.matterport.com/show/?play=1") is None
        assert sync._extract_matterport_id("") is None
        assert sync._extract_matterport_id(None) is None


class TestManufacturerInfo:
    def test_exact_match(self):
        sync = InventorySync(dry_run=True)
        info = sync._get_manufacturer_info("TRU")
        assert info["name"] == "TRU Homes"
        assert info["manufacturer_id"] == "2007"

    def test_case_insensitive_match(self):
        sync = InventorySync(dry_run=True)
        assert sync._get_manufacturer_info("tru")["name"] == "TRU Homes"

    def test_partial_match(self):
        sync = InventorySync(dry_run=True)
        assert sync._get_manufacturer_info("TRU Homes")["manufacturer_id"] == "2007"

    def test_cdn_url_lookup_by_manufacturer_id(self):
        sync = InventorySync(dry_run=True)
        url = "https://cdn.example.com/manufacturer/3335/floorplan/1/1.jpg"
        info = sync._get_manufacturer_info("Mystery Builder", cdn_url=url)
        assert info["name"] == "New Vision Manufacturing"

    def test_cdn_url_unknown_id_returns_generic(self):
        sync = InventorySync(dry_run=True)
        url = "https://cdn.example.com/manufacturer/9999/floorplan/1/1.jpg"
        info = sync._get_manufacturer_info("Mystery Builder", floorplan_url=url)
        assert info == {"name": "Mystery Builder", "manufacturer_id": "9999", "phone": None}

    def test_unknown_manufacturer_falls_back(self):
        sync = InventorySync(dry_run=True)
        info = sync._get_manufacturer_info("Nobody")
        assert info is not None


class TestPlanIdLookup:
    def test_direct_lookup(self):
        sync = InventorySync(dry_run=True)
        assert sync._get_plan_id("The Big Steve") == "225053"

    def test_partial_lookup_without_the(self):
        sync = InventorySync(dry_run=True)
        assert sync._get_plan_id("Big Steve") == "225053"

    def test_unknown_model_returns_none(self):
        sync = InventorySync(dry_run=True)
        assert sync._get_plan_id("Completely Unknown Model XYZ") is None


class TestBuildCdnUrls:
    def test_builds_numbered_and_floorplan_patterns(self):
        sync = InventorySync(dry_run=True)
        urls = sync._build_cdn_urls("2007", "222250")
        assert len(urls) == 21
        assert urls[0] == "https://d132mt2yijm03y.cloudfront.net/manufacturer/2007/floorplan/222250/1.jpg"
        assert urls[-1].endswith("/floor-plans.jpg")


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class TestScraping:
    def test_scrapes_until_empty_page(self, monkeypatch):
        pages = {
            "https://www.texashomeoutlet.com/inventory/": CARD_HTML,
            "https://www.texashomeoutlet.com/inventory/?pg=2": "<html><body></body></html>",
        }

        def fake_get(url, headers=None, timeout=None):
            assert timeout is not None
            return FakeResponse(pages[url])

        monkeypatch.setattr("tools.inventory_sync.requests.get", fake_get)
        sync = InventorySync(dry_run=True)

        homes = sync.scrape_website_inventory()

        assert len(homes) == 1
        assert homes[0]["id"] == "28102"
        assert sync.stats["scraped"] == 1

    def test_dedupes_ids_across_pages(self, monkeypatch):
        # Same card served twice; second occurrence must not double-count.
        def fake_get(url, headers=None, timeout=None):
            return FakeResponse(CARD_HTML)

        monkeypatch.setattr("tools.inventory_sync.requests.get", fake_get)

        class LimitedSync(InventorySync):
            def _inventory_page_url(self, page):
                return f"https://www.texashomeoutlet.com/inventory/?pg={page}"

        sync = LimitedSync(dry_run=True)
        # Page 1 and page 2 both list the same home -> new_on_page == 0 breaks.
        homes = sync.scrape_website_inventory()
        assert len(homes) == 1

    def test_request_error_stops_scraping(self, monkeypatch):
        def fake_get(url, headers=None, timeout=None):
            raise ConnectionError("site down")

        monkeypatch.setattr("tools.inventory_sync.requests.get", fake_get)
        sync = InventorySync(dry_run=True)

        assert sync.scrape_website_inventory() == []
        assert sync.stats["scraped"] == 0


class TestExistingInventoryLookup:
    def test_no_db_returns_none(self):
        sync = InventorySync(dry_run=True)
        assert sync.check_existing_inventory("1") is None
        assert sync.find_existing_inventory_by_model("The Delight") is None

    def test_hit_and_miss(self):
        sync = _sync_with_db({"42": {"model_name": "The Delight"}})
        hit = sync.check_existing_inventory("42")
        assert hit["id"] == "42"
        assert sync.check_existing_inventory("43") is None

    def test_lookup_error_returns_none(self):
        sync = _sync_with_db(raise_on="get")
        assert sync.check_existing_inventory("42") is None

    def test_find_by_model_normalizes_both_sides(self):
        sync = _sync_with_db({"doc-1": {"model_name": "Big Blue"}})
        found = sync.find_existing_inventory_by_model("PRE-OWNED / Big Blue")
        assert found["id"] == "doc-1"
        assert sync.find_existing_inventory_by_model("No Such Home") is None

    def test_find_by_model_empty_key(self):
        sync = _sync_with_db({"doc-1": {"model_name": "Big Blue"}})
        assert sync.find_existing_inventory_by_model("@@@") is None

    def test_find_by_model_stream_error(self):
        sync = _sync_with_db({"doc-1": {"model_name": "Big Blue"}}, raise_on="stream")
        assert sync.find_existing_inventory_by_model("Big Blue") is None


class TestSyncToFirestore:
    def test_dry_run_makes_no_rpc_calls(self):
        sync = InventorySync(dry_run=True)
        sync.db = FakeFirestore()  # even with a db, dry run must not touch it
        sync.sync_to_firestore([_item(sync), _item(sync, item_id="100")])
        assert sync.db._inventory.ops == []
        assert sync.stats["added"] == 0

    def test_without_db_aborts(self):
        sync = InventorySync(dry_run=True)
        sync.sync_to_firestore([_item(sync)])
        assert sync.stats["added"] == 0

    def test_adds_new_item(self):
        sync = _sync_with_db()
        sync.sync_to_firestore([_item(sync, item_id="99")])
        assert sync.stats["added"] == 1
        assert ("set", "99") in sync.db._inventory.ops
        assert sync.db._inventory._docs["99"]["model_name"] == "The Delight"

    def test_skips_existing_unless_forced(self):
        sync = _sync_with_db({"99": {"model_name": "The Delight"}})
        sync.sync_to_firestore([_item(sync, item_id="99")])
        assert sync.stats["skipped"] == 1
        assert sync.db._inventory.ops == []

    def test_force_updates_existing(self):
        sync = _sync_with_db({"99": {"model_name": "Old Name"}})
        sync.sync_to_firestore([_item(sync, item_id="99")], force_update=True)
        assert sync.stats["updated"] == 1
        assert ("update", "99") in sync.db._inventory.ops

    def test_model_match_routes_update_to_existing_doc(self):
        sync = _sync_with_db({"doc-1": {"model_name": "The Delight"}})
        # New listing id, but the normalized model matches an existing doc.
        sync.sync_to_firestore([_item(sync, item_id="new-99")], force_update=True)
        assert sync.stats["updated"] == 1
        assert ("update", "doc-1") in sync.db._inventory.ops

    def test_per_item_error_counted(self):
        sync = _sync_with_db(raise_on="set")
        sync.sync_to_firestore([_item(sync, item_id="99")])
        assert sync.stats["errors"] == 1


class TestRunSync:
    def _card_home(self, sync):
        card = BeautifulSoup(CARD_HTML, "html.parser").select_one(".fp-card-container")
        return sync._extract_card_data(card)

    def test_no_homes_aborts(self, monkeypatch):
        sync = InventorySync(dry_run=True)
        monkeypatch.setattr(sync, "scrape_website_inventory", lambda: [])
        stats = sync.run_sync()
        assert stats["scraped"] == 0

    def test_full_run_writes_preview_and_syncs(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        sync = InventorySync(dry_run=True)
        home = self._card_home(sync)
        monkeypatch.setattr(
            sync,
            "scrape_website_inventory",
            lambda: sync.stats.update(scraped=1) or [home],
        )

        synced = []
        monkeypatch.setattr(sync, "sync_to_firestore", lambda items, force: synced.extend(items))

        stats = sync.run_sync()

        assert stats["scraped"] == 1
        assert len(synced) == 1
        previews = list((tmp_path / "data").glob("inventory_sync_preview_*.json"))
        assert len(previews) == 1
        assert '"model_name"' in previews[0].read_text()
