"""
Asset Scraper for Texas Home Outlet.

Scrapes texashomeoutlet.com for real property images (CloudFront CDN)
and Matterport 3D tour IDs, then caches them for use in Ad Studio.

CDN pattern: https://d132mt2yijm03y.cloudfront.net/manufacturer/{id}/floorplan/{id}/{filename}
Pre-owned:   https://d132mt2yijm03y.cloudfront.net/dealer/3522/inventory/{id}/{filename}
Matterport:  https://my.matterport.com/show/?m={TOUR_ID}
"""

import re
import json
import logging
import os
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CDN_BASE = "https://d132mt2yijm03y.cloudfront.net"
MATTERPORT_BASE = "https://my.matterport.com/show/?m="
ASSET_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "property_assets.json")

# ─── Hardcoded asset catalog (scraped from texashomeoutlet.com Feb 2026) ───
# This avoids runtime scraping — we cache the known assets from the website.

# Helper for building CDN URLs per plan
def _cdn(plan_id, filename):
    return f"{CDN_BASE}/manufacturer/3335/floorplan/{plan_id}/{filename}"


PROPERTY_ASSETS = {
    # ──── NEW HOMES (galleries expanded Feb 2026) ────
    "the-big-steve": {
        "name": "The Big Steve",
        "beds": 3, "baths": 2, "sqft": 1680, "dims": "32x56",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "225053",
        "floor_plan": _cdn("225053", "the-big-steve-floor-plans.jpg"),
        "images": [
            _cdn("225053", "big-steve-kit-1.jpg"),
            _cdn("225053", "big-steve-kit-2.jpg"),
            _cdn("225053", "big-steve-kit-3.jpg"),
            _cdn("225053", "big-steve-kit-4.jpg"),
            _cdn("225053", "big-steve-kit-5.jpg"),
            _cdn("225053", "big-steve-kit-6.jpg"),
            _cdn("225053", "big-steve-kit-7.jpg"),
            _cdn("225053", "big-steve-kit-8.jpg"),
            _cdn("225053", "big-steve-int-1.jpg"),
            _cdn("225053", "big-steve-int-2.jpg"),
            _cdn("225053", "big-steve-int-3.jpg"),
            _cdn("225053", "big-steve-int-4.jpg"),
        ],
        "image_categories": {
            "kitchen": ["big-steve-kit-1.jpg", "big-steve-kit-2.jpg", "big-steve-kit-3.jpg",
                        "big-steve-kit-4.jpg", "big-steve-kit-5.jpg", "big-steve-kit-6.jpg",
                        "big-steve-kit-7.jpg", "big-steve-kit-8.jpg"],
            "interior": ["big-steve-int-1.jpg", "big-steve-int-2.jpg",
                         "big-steve-int-3.jpg", "big-steve-int-4.jpg"],
        },
        "matterport_id": "JbALitnto3g",
        "is_new": True,
    },
    "the-willison": {
        "name": "The Willison",
        "beds": 3, "baths": 2, "sqft": 1180, "dims": "16x76",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "225054",
        "floor_plan": _cdn("225054", "The-Willison-floor-plans.jpg"),
        "images": [
            _cdn("225054", "WILLISON-3.jpg"),
            _cdn("225054", "WILLISON-4.jpg"),
            _cdn("225054", "WILLISON-5.jpg"),
            _cdn("225054", "WILLISON-6.jpg"),
            _cdn("225054", "WILLISON-7.jpg"),
            _cdn("225054", "WILLISON-8.jpg"),
            _cdn("225054", "WILLISON-9.jpg"),
            _cdn("225054", "WILLISON-10.jpg"),
            _cdn("225054", "WILLISON-11.jpg"),
            _cdn("225054", "WILLISON-12.jpg"),
            _cdn("225054", "WILLISON-13.jpg"),
            _cdn("225054", "WILLISON-14.jpg"),
        ],
        "image_categories": {"interior": [
            "WILLISON-3.jpg", "WILLISON-4.jpg", "WILLISON-5.jpg", "WILLISON-6.jpg",
            "WILLISON-7.jpg", "WILLISON-8.jpg", "WILLISON-9.jpg", "WILLISON-10.jpg",
            "WILLISON-11.jpg", "WILLISON-12.jpg", "WILLISON-13.jpg", "WILLISON-14.jpg",
        ]},
        "matterport_id": "X4kxMpwHFCj",
        "is_new": True,
    },
    "the-bobby-jo": {
        "name": "The Bobby Jo",
        "beds": 2, "baths": 2, "sqft": 900, "dims": "16x66",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "225062",
        "floor_plan": _cdn("225062", "the%20bobby%20jo%20floor-plans.jpg"),
        "images": [
            _cdn("225062", "The-Bobby-Jo-Kit-1.jpg"),
            _cdn("225062", "The-Bobby-Jo-Kit-2.jpg"),
            _cdn("225062", "The-Bobby-Jo-Kit-3.jpg"),
            _cdn("225062", "The-Bobby-Jo-Kit-4.jpg"),
            _cdn("225062", "The-Bobby-Jo-Kit-5.jpg"),
            _cdn("225062", "The-Bobby-Jo-Bath-1.jpg"),
            _cdn("225062", "The-Bobby-Jo-Bath-2.jpg"),
            _cdn("225062", "The-Bobby-Jo-Bath-3.jpg"),
            _cdn("225062", "The-Bobby-Jo-Bath-4.jpg"),
            _cdn("225062", "The-Bobby-Jo-Int-1.jpg"),
            _cdn("225062", "The-Bobby-Jo-Int-2.jpg"),
            _cdn("225062", "The-Bobby-Jo-Int-3.jpg"),
        ],
        "image_categories": {
            "kitchen": ["The-Bobby-Jo-Kit-1.jpg", "The-Bobby-Jo-Kit-2.jpg",
                        "The-Bobby-Jo-Kit-3.jpg", "The-Bobby-Jo-Kit-4.jpg", "The-Bobby-Jo-Kit-5.jpg"],
            "bathroom": ["The-Bobby-Jo-Bath-1.jpg", "The-Bobby-Jo-Bath-2.jpg",
                         "The-Bobby-Jo-Bath-3.jpg", "The-Bobby-Jo-Bath-4.jpg"],
            "interior": ["The-Bobby-Jo-Int-1.jpg", "The-Bobby-Jo-Int-2.jpg", "The-Bobby-Jo-Int-3.jpg"],
        },
        "matterport_id": "soxcQGiu2Zg",
        "is_new": True,
    },
    "the-vail": {
        "name": "The Vail (Park Model)",
        "beds": 1, "baths": 1, "sqft": 396, "dims": "12x34",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "225063",
        "floor_plan": _cdn("225063", "the-vail-floor-plans.jpg"),
        "images": [
            _cdn("225063", "The-Vail-1.jpg"),
            _cdn("225063", "The-Vail-2.png"),
            _cdn("225063", "The-Vail-3.png"),
            _cdn("225063", "The-Vail-4.png"),
            _cdn("225063", "The-Vail-5.png"),
            _cdn("225063", "The-Vail-6.png"),
            _cdn("225063", "The-Vail-7.png"),
            _cdn("225063", "The-Vail-8.png"),
            _cdn("225063", "The-Vail-9.png"),
            _cdn("225063", "The-Vail-10.jpg"),
            _cdn("225063", "The-Vail-11.png"),
            _cdn("225063", "The-Vail-12.jpg"),
        ],
        "image_categories": {
            "interior": ["The-Vail-1.jpg", "The-Vail-2.png", "The-Vail-3.png", "The-Vail-4.png",
                         "The-Vail-5.png", "The-Vail-6.png", "The-Vail-7.png", "The-Vail-8.png",
                         "The-Vail-9.png", "The-Vail-10.jpg", "The-Vail-11.png"],
            "exterior": ["The-Vail-12.jpg"],
        },
        "matterport_id": "vBEByY7x1sL",
        "is_new": True,
    },
    "the-whitehaven": {
        "name": "The Whitehaven",
        "beds": 3, "baths": 2, "sqft": 1568, "dims": "28x56",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "227079",
        "floor_plan": _cdn("227079", "Whitehaven%20floor-plans.jpg"),
        "images": [
            _cdn("227079", "1.jpg"),
            _cdn("227079", "2.png"),
            _cdn("227079", "3.jpg"),
            _cdn("227079", "4.jpg"),
            _cdn("227079", "5.jpg"),
            _cdn("227079", "6.jpg"),
            _cdn("227079", "7.jpg"),
            _cdn("227079", "8.jpg"),
            _cdn("227079", "9.jpg"),
            _cdn("227079", "10.jpg"),
            _cdn("227079", "11.jpg"),
            _cdn("227079", "12.jpg"),
            _cdn("227079", "13.jpg"),
        ],
        "image_categories": {"interior": [
            "1.jpg", "2.png", "3.jpg", "4.jpg", "5.jpg", "6.jpg", "7.jpg",
            "8.jpg", "9.jpg", "10.jpg", "11.jpg", "12.jpg", "13.jpg",
        ]},
        "matterport_id": "tMC8frQ8vvH",
        "is_new": True,
    },
    "the-sherman": {
        "name": "The Sherman",
        "beds": 4, "baths": 2, "sqft": 1944, "dims": "28x72",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "227080",
        "floor_plan": _cdn("227080", "sherman%20floor-plans-SMALL.jpg"),
        "images": [
            _cdn("227080", "1.png"),
            _cdn("227080", "2.png"),
            _cdn("227080", "3.jpg"),
            _cdn("227080", "4.png"),
            _cdn("227080", "5.png"),
            _cdn("227080", "6.png"),
            _cdn("227080", "7.png"),
            _cdn("227080", "8.png"),
            _cdn("227080", "9.png"),
            _cdn("227080", "10.png"),
            _cdn("227080", "11.png"),
            _cdn("227080", "12.png"),
            _cdn("227080", "13.png"),
            _cdn("227080", "14.png"),
        ],
        "image_categories": {"interior": [
            "1.png", "2.png", "3.jpg", "4.png", "5.png", "6.png", "7.png",
            "8.png", "9.png", "10.png", "11.png", "12.png", "13.png", "14.png",
        ]},
        "matterport_id": "28Zpa86S53Q",
        "is_new": True,
    },
    "the-fiesta": {
        "name": "The Fiesta",
        "beds": 3, "baths": 2, "sqft": 1344, "dims": "28x48",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "227313",
        "floor_plan": _cdn("227313", "fiesta%20fixed%20floor-plans-SMALL.jpg"),
        "images": [
            _cdn("227313", "The-Fiesta-Kit-1.jpg"),
            _cdn("227313", "The-Fiesta-Kit-2.jpg"),
            _cdn("227313", "The-Fiesta-Kit-3.jpg"),
            _cdn("227313", "The-Fiesta-Kit-4.jpg"),
            _cdn("227313", "The-Fiesta-Kit-5.jpg"),
            _cdn("227313", "The-Fiesta-Kit-6.jpg"),
            _cdn("227313", "The-Fiesta-Kit-7.jpg"),
            _cdn("227313", "The-Fiesta-Bath-1.jpg"),
            _cdn("227313", "The-Fiesta-Int-1.jpg"),
            _cdn("227313", "The-Fiesta-Int-2.jpg"),
            _cdn("227313", "The-Fiesta-Int-3.jpg"),
            _cdn("227313", "The-Fiesta-Int-4.jpg"),
        ],
        "image_categories": {
            "kitchen": ["The-Fiesta-Kit-1.jpg", "The-Fiesta-Kit-2.jpg", "The-Fiesta-Kit-3.jpg",
                        "The-Fiesta-Kit-4.jpg", "The-Fiesta-Kit-5.jpg", "The-Fiesta-Kit-6.jpg",
                        "The-Fiesta-Kit-7.jpg"],
            "bathroom": ["The-Fiesta-Bath-1.jpg"],
            "interior": ["The-Fiesta-Int-1.jpg", "The-Fiesta-Int-2.jpg",
                         "The-Fiesta-Int-3.jpg", "The-Fiesta-Int-4.jpg"],
        },
        "matterport_id": "dHUHPwxVnLg",
        "is_new": True,
    },
    "the-charleston": {
        "name": "The Charleston",
        "beds": 3, "baths": 2, "sqft": 1815, "dims": "28x66",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "231478",
        "floor_plan": _cdn("231478", "The%20Charleston%20floor-plans-SMALL%20%282%29.jpg"),
        "images": [
            _cdn("231478", "1.jpg"),
            _cdn("231478", "2.jpg"),
            _cdn("231478", "3.jpg"),
            _cdn("231478", "4.jpg"),
            _cdn("231478", "5.jpg"),
            _cdn("231478", "6.jpg"),
            _cdn("231478", "7.jpg"),
            _cdn("231478", "8.jpg"),
            _cdn("231478", "9.jpg"),
            _cdn("231478", "10.jpg"),
            _cdn("231478", "11.jpg"),
            _cdn("231478", "12.jpg"),
            _cdn("231478", "13.jpg"),
        ],
        "image_categories": {"interior": [
            "1.jpg", "2.jpg", "3.jpg", "4.jpg", "5.jpg", "6.jpg", "7.jpg",
            "8.jpg", "9.jpg", "10.jpg", "11.jpg", "12.jpg", "13.jpg",
        ]},
        "matterport_id": "qChSMQi5t5E",
        "is_new": True,
    },
    "the-tony": {
        "name": "The Tony",
        "beds": 3, "baths": 2, "sqft": 1012, "dims": "16x76",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "231916",
        "floor_plan": _cdn("231916", "the%20tony%20floor-plans-SMALL.png"),
        "images": [
            _cdn("231916", "Tony-1.jpg"),
            _cdn("231916", "Tony-2.jpg"),
            _cdn("231916", "Tony-3.jpg"),
            _cdn("231916", "Tony-4.jpg"),
            _cdn("231916", "Tony-5.jpg"),
            _cdn("231916", "Tony-6.jpg"),
            _cdn("231916", "Tony-7.jpg"),
            _cdn("231916", "Tony-8.jpg"),
            _cdn("231916", "Tony-9.jpg"),
            _cdn("231916", "Tony-10.jpg"),
            _cdn("231916", "Tony-11.jpg"),
            _cdn("231916", "Tony-12.jpg"),
            _cdn("231916", "Tony-13.jpg"),
        ],
        "image_categories": {"interior": [
            "Tony-1.jpg", "Tony-2.jpg", "Tony-3.jpg", "Tony-4.jpg", "Tony-5.jpg",
            "Tony-6.jpg", "Tony-7.jpg", "Tony-8.jpg", "Tony-9.jpg", "Tony-10.jpg",
            "Tony-11.jpg", "Tony-12.jpg", "Tony-13.jpg",
        ]},
        "matterport_id": "HtLxXhBdNmG",
        "is_new": True,
    },
    "the-stephens": {
        "name": "The Stephens",
        "beds": 3, "baths": 2, "sqft": 1680, "dims": "28x64",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "232171",
        "floor_plan": _cdn("232171", "The%20Stephens%20floor-plans-SMALL.jpg"),
        "images": [
            _cdn("232171", "THO-The%20Stephens-kit-1_thumb_xl.jpg"),
            _cdn("232171", "THO-The%20Stephens-kit-2_thumb_xl.jpg"),
            _cdn("232171", "THO-The%20Stephens-kit-3_thumb_xl.jpg"),
            _cdn("232171", "THO-The%20Stephens-bed-1_thumb_xl.jpg"),
            _cdn("232171", "THO-The%20Stephens-bed-2_thumb_xl.jpg"),
            _cdn("232171", "THO-The%20Stephens-int-1.jpg"),
            _cdn("232171", "THO-The%20Stephens-int-2_thumb_xl.jpg"),
            _cdn("232171", "THO-The%20Stephens-int-3_thumb_xl.jpg"),
            _cdn("232171", "THO-The%20Stephens-int-4_thumb_xl.jpg"),
        ],
        "image_categories": {
            "kitchen": ["THO-The%20Stephens-kit-1_thumb_xl.jpg",
                        "THO-The%20Stephens-kit-2_thumb_xl.jpg",
                        "THO-The%20Stephens-kit-3_thumb_xl.jpg"],
            "bedroom": ["THO-The%20Stephens-bed-1_thumb_xl.jpg",
                        "THO-The%20Stephens-bed-2_thumb_xl.jpg"],
            "interior": ["THO-The%20Stephens-int-1.jpg",
                         "THO-The%20Stephens-int-2_thumb_xl.jpg",
                         "THO-The%20Stephens-int-3_thumb_xl.jpg",
                         "THO-The%20Stephens-int-4_thumb_xl.jpg"],
        },
        "matterport_id": "k3YazXae32T",
        "is_new": True,
    },
    "the-copperwood": {
        "name": "The Copperwood (Park Model)",
        "beds": 1, "baths": 1, "sqft": 396, "dims": "12x34",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "232172",
        "floor_plan": _cdn("232172", "The%20Copperwood%20floor-plans-SMALL.png"),
        "images": [
            _cdn("232172", "new-1.png"),
            _cdn("232172", "new-3.png"),
            _cdn("232172", "new-4.png"),
            _cdn("232172", "new-5.png"),
            _cdn("232172", "new-6.png"),
            _cdn("232172", "new-7.png"),
            _cdn("232172", "new-8.png"),
            _cdn("232172", "new-9.png"),
            _cdn("232172", "10.jpg"),
            _cdn("232172", "11.png"),
            _cdn("232172", "12.png"),
            _cdn("232172", "13.png"),
        ],
        "image_categories": {"interior": [
            "new-1.png", "new-3.png", "new-4.png", "new-5.png", "new-6.png",
            "new-7.png", "new-8.png", "new-9.png", "10.jpg", "11.png", "12.png", "13.png",
        ]},
        "matterport_id": "ZCSYfEbBY3W",
        "is_new": True,
    },
    "the-anderson": {
        "name": "The Anderson (Park Model)",
        "beds": 1, "baths": 1, "sqft": 396, "dims": "12x40",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "232844",
        "floor_plan": _cdn("232844", "The%20Anderson%20floor-plans-SMALL.jpg"),
        "images": [
            _cdn("232844", "anderson-1.jpg"),
            _cdn("232844", "anderson-2.jpg"),
            _cdn("232844", "anderson-3.jpg"),
            _cdn("232844", "anderson-4.jpg"),
            _cdn("232844", "anderson-5.jpg"),
            _cdn("232844", "anderson-6.jpg"),
            _cdn("232844", "anderson-7.jpg"),
            _cdn("232844", "anderson-8.jpg"),
            _cdn("232844", "anderson-9.jpg"),
            _cdn("232844", "anderson-10.jpg"),
            _cdn("232844", "anderson-11.jpg"),
        ],
        "image_categories": {"interior": [
            "anderson-1.jpg", "anderson-2.jpg", "anderson-3.jpg", "anderson-4.jpg",
            "anderson-5.jpg", "anderson-6.jpg", "anderson-7.jpg", "anderson-8.jpg",
            "anderson-9.jpg", "anderson-10.jpg", "anderson-11.jpg",
        ]},
        "matterport_id": "PaB34i7g27v",
        "is_new": True,
    },
    "the-suite-sara": {
        "name": "The Suite Sara",
        "beds": 4, "baths": 2, "sqft": 1560, "dims": "30x52",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "234918",
        "floor_plan": _cdn("234918", "Suite%20Sara.jpg"),
        "images": [
            _cdn("234918", "New%20Vision%20The%20Suite%20Sara-kit-1.jpg"),
            _cdn("234918", "New%20Vision%20The%20Suite%20Sara-kit-2.jpg"),
            _cdn("234918", "New%20Vision%20The%20Suite%20Sara-kit-3.jpg"),
            _cdn("234918", "New%20Vision%20The%20Suite%20Sara-kit-4.jpg"),
            _cdn("234918", "New%20Vision%20The%20Suite%20Sara-bed-1.jpg"),
            _cdn("234918", "New%20Vision%20The%20Suite%20Sara-bed-2.jpg"),
            _cdn("234918", "New%20Vision%20The%20Suite%20Sara-bed-3.jpg"),
            _cdn("234918", "New%20Vision%20The%20Suite%20Sara-int-1.jpg"),
            _cdn("234918", "New%20Vision%20The%20Suite%20Sara-int-2.jpg"),
            _cdn("234918", "New%20Vision%20The%20Suite%20Sara-int-3.jpg"),
        ],
        "image_categories": {
            "kitchen": ["New%20Vision%20The%20Suite%20Sara-kit-1.jpg",
                        "New%20Vision%20The%20Suite%20Sara-kit-2.jpg",
                        "New%20Vision%20The%20Suite%20Sara-kit-3.jpg",
                        "New%20Vision%20The%20Suite%20Sara-kit-4.jpg"],
            "bedroom": ["New%20Vision%20The%20Suite%20Sara-bed-1.jpg",
                        "New%20Vision%20The%20Suite%20Sara-bed-2.jpg",
                        "New%20Vision%20The%20Suite%20Sara-bed-3.jpg"],
            "interior": ["New%20Vision%20The%20Suite%20Sara-int-1.jpg",
                         "New%20Vision%20The%20Suite%20Sara-int-2.jpg",
                         "New%20Vision%20The%20Suite%20Sara-int-3.jpg"],
        },
        "matterport_id": "JkYCD7EonBP",
        "is_new": True,
    },
    "the-big-josh": {
        "name": "The Big Josh",
        "beds": 4, "baths": 3, "sqft": 2280, "dims": "32x76",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "235402",
        "floor_plan": _cdn("235402", "The%20Big%20Josh.jpg"),
        "images": [
            _cdn("235402", "The%20Big%20Josh-1.png"),
            _cdn("235402", "The%20Big%20Josh-2.png"),
            _cdn("235402", "The%20Big%20Josh-3.png"),
            _cdn("235402", "The%20Big%20Josh-4.png"),
            _cdn("235402", "The%20Big%20Josh-5.png"),
            _cdn("235402", "The%20Big%20Josh-6.png"),
            _cdn("235402", "The%20Big%20Josh-7.png"),
            _cdn("235402", "The%20Big%20Josh-8.png"),
            _cdn("235402", "The%20Big%20Josh-9.png"),
            _cdn("235402", "The%20Big%20Josh-10.png"),
            _cdn("235402", "The%20Big%20Josh-11.png"),
            _cdn("235402", "The%20Big%20Josh-12.png"),
        ],
        "image_categories": {"interior": [
            "The%20Big%20Josh-1.png", "The%20Big%20Josh-2.png", "The%20Big%20Josh-3.png",
            "The%20Big%20Josh-4.png", "The%20Big%20Josh-5.png", "The%20Big%20Josh-6.png",
            "The%20Big%20Josh-7.png", "The%20Big%20Josh-8.png", "The%20Big%20Josh-9.png",
            "The%20Big%20Josh-10.png", "The%20Big%20Josh-11.png", "The%20Big%20Josh-12.png",
        ]},
        "matterport_id": "nMza1rTBGKi",
        "is_new": True,
    },
    "the-big-bo": {
        "name": "The Big Bo",
        "beds": 3, "baths": 2, "sqft": 1360, "dims": "18x80",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": "235403",
        "floor_plan": _cdn("235403", "The%20Big%20Bo.jpg"),
        "images": [
            _cdn("235403", "1.jpg"),
            _cdn("235403", "2.jpg"),
            _cdn("235403", "3.jpg"),
            _cdn("235403", "4.jpg"),
            _cdn("235403", "5.jpg"),
            _cdn("235403", "6.jpg"),
            _cdn("235403", "7.jpg"),
            _cdn("235403", "8.jpg"),
            _cdn("235403", "9.jpg"),
            _cdn("235403", "10.jpg"),
            _cdn("235403", "11.jpg"),
            _cdn("235403", "12.jpg"),
        ],
        "image_categories": {"interior": [
            "1.jpg", "2.jpg", "3.jpg", "4.jpg", "5.jpg", "6.jpg",
            "7.jpg", "8.jpg", "9.jpg", "10.jpg", "11.jpg", "12.jpg",
        ]},
        "matterport_id": "8DsvhvabUqW",
        "is_new": True,
    },
    "the-fiesta-2": {
        "name": "The Fiesta 2.0",
        "beds": 3, "baths": 2, "sqft": 1296, "dims": "28x56",
        "manufacturer": "New Vision Manufacturing",
        "manufacturer_id": "3335", "plan_id": None,
        "floor_plan": None,
        "images": [],
        "matterport_id": "ib9PxMThwCy",
        "is_new": True,
    },
    # ──── PRE-OWNED HOMES ────
    "big-blue": {
        "name": "Big Blue",
        "beds": 3, "baths": 2, "sqft": 1152, "dims": "16x72",
        "manufacturer": "Pre-Owned",
        "manufacturer_id": None, "plan_id": None,
        "floor_plan": f"{CDN_BASE}/dealer/3522/inventory/44490/floor-plans-SMALL.jpg",
        "images": [f"{CDN_BASE}/dealer/3522/inventory/44490/floor-plans-SMALL.jpg"],
        "image_categories": {},
        "matterport_id": None,
        "is_new": False,
    },
    "the-cottage": {
        "name": "The Cottage",
        "beds": 2, "baths": 1, "sqft": 864, "dims": "16x54",
        "manufacturer": "Pre-Owned",
        "manufacturer_id": None, "plan_id": None,
        "floor_plan": f"{CDN_BASE}/dealer/3522/inventory/44491/floor-plans-SMALL%20%281%29.jpg",
        "images": [f"{CDN_BASE}/dealer/3522/inventory/44491/floor-plans-SMALL%20%281%29.jpg"],
        "image_categories": {},
        "matterport_id": None,
        "is_new": False,
    },
    "select-legacy-s-2468": {
        "name": "Select Legacy S-2468-42A",
        "beds": 4, "baths": 2, "sqft": 1483, "dims": "24x68",
        "manufacturer": "Pre-Owned",
        "manufacturer_id": "1944", "plan_id": "223000",
        "floor_plan": f"{CDN_BASE}/manufacturer/1944/floorplan/223000/S-2468-42A-floor-plans.jpg",
        "images": [f"{CDN_BASE}/manufacturer/1944/floorplan/223000/S-2468-42A-floor-plans.jpg"],
        "image_categories": {},
        "matterport_id": "r7T4g8z8iLo",
        "is_new": False,
    },
}


def get_all_assets() -> dict:
    """Return the full property asset catalog."""
    total_images = sum(len(p.get("images", [])) for p in PROPERTY_ASSETS.values())
    with_bedroom = sum(1 for p in PROPERTY_ASSETS.values()
                       if "bedroom" in p.get("image_categories", {}))
    with_bathroom = sum(1 for p in PROPERTY_ASSETS.values()
                        if "bathroom" in p.get("image_categories", {}))
    return {
        "success": True,
        "properties": PROPERTY_ASSETS,
        "total": len(PROPERTY_ASSETS),
        "total_images": total_images,
        "with_matterport": sum(1 for p in PROPERTY_ASSETS.values() if p.get("matterport_id")),
        "with_images": sum(1 for p in PROPERTY_ASSETS.values() if p.get("images")),
        "with_bedroom_photos": with_bedroom,
        "with_bathroom_photos": with_bathroom,
        "cdn_base": CDN_BASE,
        "matterport_base": MATTERPORT_BASE,
    }


# ─── Alias mapping: Inventory model names → website display names ───
# Bridges the gap between manufacturer model IDs and friendly names on texashomeoutlet.com
INVENTORY_ALIASES = {
    # Map known inventory model numbers to asset scraper slugs
    # Add more as inventory/website correlation is discovered
    "select legacy s-2468-42a": "select-legacy-s-2468",
}


def get_assets_for_home(home_name: str) -> Optional[dict]:
    """Look up assets for a home by name (fuzzy match)."""
    name_lower = home_name.lower().strip()

    # Check alias mapping first (inventory model name → website slug)
    alias_slug = INVENTORY_ALIASES.get(name_lower)
    if alias_slug and alias_slug in PROPERTY_ASSETS:
        return PROPERTY_ASSETS[alias_slug]

    # Direct slug match
    for slug, asset in PROPERTY_ASSETS.items():
        if slug == name_lower.replace(" ", "-"):
            return asset

    # Name contains match
    for slug, asset in PROPERTY_ASSETS.items():
        if name_lower in asset["name"].lower() or asset["name"].lower() in name_lower:
            return asset

    # Partial word match (at least 2 non-common words must match)
    for slug, asset in PROPERTY_ASSETS.items():
        name_words = set(name_lower.split())
        asset_words = set(asset["name"].lower().split())
        common = {"the", "a", "an", "model", "park"}
        meaningful_matches = (name_words & asset_words) - common
        if len(meaningful_matches) >= 1:
            return asset

    return None


def get_matterport_url(tour_id: str) -> str:
    """Build a Matterport embed URL from a tour ID."""
    return f"{MATTERPORT_BASE}{tour_id}&play=1"


def get_homes_with_tours() -> list:
    """Return all homes that have Matterport 3D tours."""
    return [
        {
            "name": asset["name"],
            "slug": slug,
            "matterport_id": asset["matterport_id"],
            "matterport_url": get_matterport_url(asset["matterport_id"]),
            "beds": asset["beds"],
            "baths": asset["baths"],
            "sqft": asset["sqft"],
            "floor_plan": asset.get("floor_plan"),
            "image_count": len(asset.get("images", [])),
            "room_categories": list(asset.get("image_categories", {}).keys()),
            "is_new": asset["is_new"],
        }
        for slug, asset in PROPERTY_ASSETS.items()
        if asset.get("matterport_id")
    ]
