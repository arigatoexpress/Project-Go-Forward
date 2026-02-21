import requests
from bs4 import BeautifulSoup
import json
import time
import os
import re

BASE_URL = "https://www.texashomeoutlet.com"
INVENTORY_URL = f"{BASE_URL}/inventory/"
OUTPUT_FILE = "../data/scraped_inventory.json"

def get_soup(url):
    """Fetches a URL and returns a BeautifulSoup object."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def extract_listing_data(card):
    """Extracts data from a single inventory card."""
    try:
        # Title and Link
        title_tag = card.select_one('h4.home-name a')
        if not title_tag:
            return None
            
        title = title_tag.get_text(strip=True)
        detail_url = BASE_URL + title_tag['href'] if title_tag['href'].startswith('/') else title_tag['href']
        
        # ID from URL
        # URL format: /inventory-detail/28102/...
        listing_id = "unknown"
        match = re.search(r'/inventory-detail/(\d+)/', detail_url)
        if match:
            listing_id = match.group(1)

        # Manufacturer
        manufacturer = "Unknown"
        built_by_tag = card.select_one('.fp-card-built-by')
        if built_by_tag:
            text = built_by_tag.get_text(strip=True)
            if "Built by:" in text:
                manufacturer = text.replace("Built by:", "").strip()

        # Specs
        specs = {
            "beds": 0,
            "baths": 0,
            "sq_ft": 0,
            "width": 0,
            "length": 0
        }
        
        # Specs are in .fp-card-specs columns
        # We look for icons to identify the data
        
        # Beds
        bed_icon = card.select_one('.icon-mfh-bed')
        if bed_icon and bed_icon.parent:
            text = bed_icon.parent.get_text(strip=True)
            match = re.search(r'(\d+)', text)
            if match:
                specs['beds'] = int(match.group(1))

        # Baths
        bath_icon = card.select_one('.icon-mfh-bath')
        if bath_icon and bath_icon.parent:
            text = bath_icon.parent.get_text(strip=True)
            match = re.search(r'(\d+(\.\d+)?)', text)
            if match:
                specs['baths'] = float(match.group(1))

        # Sq Ft
        sqft_icon = card.select_one('.icon-mfh-tapemeasure')
        if sqft_icon and sqft_icon.parent:
            text = sqft_icon.parent.get_text(strip=True)
            match = re.search(r'(\d+,?\d*)', text)
            if match:
                specs['sq_ft'] = int(match.group(1).replace(',', ''))

        # Dimensions
        dim_icon = card.select_one('.icon-mfh-lengthwidth')
        if dim_icon and dim_icon.parent:
            text = dim_icon.parent.get_text(strip=True)
            match = re.search(r"(\d+)'\s*\d*\"?\s*x\s*(\d+)'", text)
            if match:
                specs['width'] = int(match.group(1))
                specs['length'] = int(match.group(2))

        # Image
        image_url = None
        # Use data-bg attribute
        image_div = card.select_one('.fp-card-image')
        if image_div and image_div.has_attr('data-bg'):
            image_url = image_div['data-bg']
            # Sometimes url might be relative or full
            if image_url.startswith('/'):
                 image_url = BASE_URL + image_url
        
        # Floorplan
        floorplan_url = None
        fp_link = card.select_one('a.fp-thumb')
        if fp_link and fp_link.has_attr('href'):
             floorplan_url = fp_link['href']

        return {
            "id": listing_id,
            "model_name": title,
            "manufacturer": manufacturer,
            "detail_url": detail_url,
            "specs": specs,
            "pricing": {
                "display_price": "Call for Price",
                "monthly_payment": "Contact for financing"
            },
            "image_url": image_url,
            "floorplan_url": floorplan_url,
            "gallery_images": [image_url] if image_url else []
        }

    except Exception as e:
        print(f"Error extracting listing: {e}")
        return None

def scrape_inventory():
    """Main scraping loop."""
    all_listings = []
    seen_ids = set()
    page = 1
    max_pages = 20 # Safety limit
    
    print("Starting inventory scrape...")
    
    while page <= max_pages:
        url = f"{INVENTORY_URL}?pg={page}"
        print(f"Scraping Page {page}...")
        
        soup = get_soup(url)
        if not soup:
            break
            
        cards = soup.select('.fp-card-container')
            
        if not cards:
            print("No more listings found (no cards).")
            break
            
        print(f"Found {len(cards)} listings on page {page}.")
        
        new_listings_on_page = 0
        
        for card in cards:
            listing = extract_listing_data(card)
            if listing:
                if listing['id'] in seen_ids:
                    continue
                
                seen_ids.add(listing['id'])
                all_listings.append(listing)
                new_listings_on_page += 1
                print(f"  - Scraped: {listing['model_name']} ({listing['id']})")
        
        # Stop if no new listings were found on this page
        if new_listings_on_page == 0:
            print("No new listings found on this page. Termination condition met.")
            break
        
        page += 1
        time.sleep(1)

    # Save to file
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(all_listings, f, indent=2)
    
    print(f"Scrape complete! Saved {len(all_listings)} unique listings to {OUTPUT_FILE}")

if __name__ == "__main__":
    scrape_inventory()
