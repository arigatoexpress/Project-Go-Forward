from bs4 import BeautifulSoup

from tools import legacy_site_crawler

CARD_HTML = """
<div class="fp-card-container">
  <div class="fp-card-tags"><span class="label">Manufactured</span><span class="label">Lot Model</span><span class="label">New</span></div>
  <div class="fp-card-image" data-bg="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/Creole%203256H32447-kit-1_card_lg.jpg"></div>
  <a class="fp-thumb" href="https://d132mt2yijm03y.cloudfront.net/manufacturer/3373/floorplan/235424/Creole%203256H32447.jpg"></a>
  <a class="tour-thumb" href="https://my.matterport.com/show/?m=mTvc6YoSRTx"></a>
  <h4 class="home-name"><a href="/inventory-detail/43372/texas-home-outlet/huffman/premier/">Premier / Creole 3256H32447</a></h4>
  <div class="fp-card-built-by">Built by: Champion Homes</div>
  <div><i class="icon-mfh-bed"></i>3</div>
  <div><i class="icon-mfh-bath"></i>2.00</div>
  <div><i class="icon-mfh-tapemeasure"></i>1,699 ft²</div>
  <div><i class="icon-mfh-lengthwidth"></i>30'4" x 56'0"</div>
  <a href="/inventory-detail/43372/texas-home-outlet/huffman/premier">More Info</a>
  <a href="/quote/inventory/3522/43372">Price Quote</a>
</div>
"""


DETAIL_HTML = """
<html>
  <head><meta name="description" content="The Creole 3256H32447 is a 3 bed home." /></head>
  <body>
    <h1>Premier / Creole 3256H32447</h1>
    <div class="detail-info">
      Beds:<span>3</span>
      Baths:<span>2.00</span>
      Sq. Ft.:<span>1699</span>
      WxL:<span>30′ 4″ x 56′ 0″</span>
      Built by:<span>Champion Homes</span>
      Price:<span>Call for pricing</span>
    </div>
    <a href="/quote/inventory/43372/dealer/3522/">Price Quote</a>
    <figure>
      <a href="https://d132mt2yijm03y.cloudfront.net/manufacturer/3373/floorplan/235424/Creole%203256H32447.jpg">
        <img src="https://d132mt2yijm03y.cloudfront.net/manufacturer/3373/floorplan/235424/Creole%203256H32447_thumb_xl.jpg" />
      </a>
      <figcaption>Layout</figcaption>
    </figure>
    <figure>
      <a href="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/Creole%203256H32447-kit-1.jpg">
        <img src="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/Creole%203256H32447-kit-1_thumb_xl.jpg" />
      </a>
      <figcaption>Kitchen</figcaption>
    </figure>
    <figure>
      <a href="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/Creole%203256H32447-ext-1.jpg">
        <img src="https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/Creole%203256H32447-ext-1_thumb_xl.jpg" />
      </a>
      <figcaption>Exterior</figcaption>
    </figure>
    <a href="https://my.matterport.com/show/?play=1&amp;m=mTvc6YoSRTx">Matterport Tour</a>
    <script>window.related = "https://d132mt2yijm03y.cloudfront.net/manufacturer/3290/floorplan/198104/de_Vaca_S64F.jpg";</script>
  </body>
</html>
"""


def test_extract_inventory_card_keeps_legacy_actions_and_full_card_media():
    card = BeautifulSoup(CARD_HTML, "html.parser").select_one(".fp-card-container")

    home = legacy_site_crawler.extract_inventory_card(card)

    assert home["id"] == "43372"
    assert home["model_name"] == "Premier / Creole 3256H32447"
    assert home["manufacturer"] == "Champion Homes"
    assert (
        home["detail_url"]
        == "https://www.texashomeoutlet.com/inventory-detail/43372/texas-home-outlet/huffman/premier/"
    )
    assert home["quote_url"] == "https://www.texashomeoutlet.com/quote/inventory/3522/43372"
    assert home["image_url"].endswith("Creole%203256H32447-kit-1.jpg")
    assert "_card_lg" not in home["image_url"]
    assert home["matterport_id"] == "mTvc6YoSRTx"
    assert home["specs"]["beds"] == 3
    assert home["specs"]["sq_ft"] == 1699


def test_extract_detail_media_filters_thumbnails_and_unrelated_floorplans():
    media = legacy_site_crawler.extract_detail_media(
        DETAIL_HTML,
        listing_id="43372",
        detail_url="https://www.texashomeoutlet.com/inventory-detail/43372/texas-home-outlet/huffman/premier/",
    )

    assert media["model_name"] == "Premier / Creole 3256H32447"
    assert (
        media["quote_url"] == "https://www.texashomeoutlet.com/quote/inventory/43372/dealer/3522/"
    )
    assert media["floorplan_url"].endswith("Creole%203256H32447.jpg")
    assert "de_Vaca_S64F.jpg" not in " ".join(media["floorplan_urls"])
    assert media["photos"] == [
        "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/Creole%203256H32447-ext-1.jpg",
        "https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/43372/Creole%203256H32447-kit-1.jpg",
    ]
    assert media["matterport_id"] == "mTvc6YoSRTx"
    assert "_thumb_xl" not in " ".join(media["photos"])
    assert media["image_categories"]["exterior"][0].endswith("ext-1.jpg")
    assert media["image_categories"]["kitchen"][0].endswith("kit-1.jpg")


def test_merged_legacy_home_exposes_provenance_and_honest_media_quality():
    card = BeautifulSoup(CARD_HTML, "html.parser").select_one(".fp-card-container")
    home = legacy_site_crawler.extract_inventory_card(card)
    media = legacy_site_crawler.extract_detail_media(
        DETAIL_HTML,
        listing_id="43372",
        detail_url="https://www.texashomeoutlet.com/inventory-detail/43372/texas-home-outlet/huffman/premier/",
    )

    merged = legacy_site_crawler._merge_card_and_detail(home, media)

    assert merged["source"] == legacy_site_crawler.LEGACY_SOURCE_ID
    assert merged["source_provenance"]["source_owner"] == "Texas Home Outlet"
    assert "no generic stock substitution" in merged["source_provenance"]["output_policy"]
    assert merged["legacy_actions"]["quote_url"].endswith("/43372/dealer/3522/")
    assert merged["image_url"].endswith("ext-1.jpg")
    assert merged["media_quality"]["status"] == "limited_photos"
    assert merged["media_quality"]["photo_count"] == 2
    assert merged["floor_plan_url"].endswith("Creole%203256H32447.jpg")


def test_legacy_context_recovers_known_floorplan_only_manufacturer_galleries(monkeypatch):
    floorplan_only = [
        {
            "id": "28102",
            "legacy_inventory_id": "28102",
            "model_name": "TRU Single Section / TRU Single Section Delight",
            "status": "Available",
            "floorplan_url": "https://d132mt2yijm03y.cloudfront.net/manufacturer/2007/floorplan/222250/DELIGHT.jpg",
            "real_photos": [],
            "gallery_images": [],
            "source_provenance": {"source_owner": "Texas Home Outlet"},
        },
        {
            "id": "42156",
            "legacy_inventory_id": "42156",
            "model_name": "The Elite Series / The Jackson ELS16763D",
            "status": "Available",
            "floorplan_url": "https://d132mt2yijm03y.cloudfront.net/manufacturer/3328/floorplan/234947/jackson.jpg",
            "real_photos": [],
            "gallery_images": [],
            "source_provenance": {"source_owner": "Texas Home Outlet"},
        },
        {
            "id": "43942",
            "legacy_inventory_id": "43942",
            "model_name": "Solution / The Pt 78 SLT28563D",
            "status": "Available",
            "floorplan_url": "https://d132mt2yijm03y.cloudfront.net/manufacturer/2025/floorplan/226548/floor-plans-SMALL%20%282%29.jpg",
            "real_photos": [],
            "gallery_images": [],
            "source_provenance": {"source_owner": "Texas Home Outlet"},
        },
        {
            "id": "28527",
            "legacy_inventory_id": "28527",
            "model_name": "PRE-OWNED / Heritage 1672-32C",
            "status": "Pre-Owned",
            "floorplan_url": "https://d132mt2yijm03y.cloudfront.net/manufacturer/1944/floorplan/1383/1672-32C-floor-plans.jpg",
            "real_photos": [],
            "gallery_images": [],
            "source_provenance": {"source_owner": "Texas Home Outlet"},
        },
    ]

    monkeypatch.setattr(
        legacy_site_crawler,
        "scrape_legacy_inventory",
        lambda max_pages=25, include_details=True: [dict(home) for home in floorplan_only],
    )

    context = legacy_site_crawler.build_legacy_inventory_context()

    assert context["media_summary"] == {
        "photo_ready": 4,
        "limited_photos": 0,
        "floorplan_only": 0,
        "missing_photos": 0,
    }
    for home in context["homes"]:
        assert home["image_url"]
        assert len(home["real_photos"]) >= 3
        assert home["media_quality"]["status"] == "photo_ready"
        assert home["media_quality"]["recovered"] is True
        assert home["media_recovery"]["source"] == "exact_manufacturer_plan_cdn"
        assert "no generic stock substitution" in home["media_recovery"]["output_policy"]
