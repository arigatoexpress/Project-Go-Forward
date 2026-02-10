"""
Marketing Tools for {{BUSINESS_NAME}} AI Marketing Agent ("Tex").

These tools enable autonomous content generation and social media posting
for TikTok, Instagram, and Facebook.
"""

from google.adk.tools import ToolContext
from typing import Optional, List
from datetime import datetime
import uuid
import random


# Content categories for manufactured homes
CONTENT_THEMES = [
    "home_tour",           # Virtual walkthrough of a specific model
    "myth_busting",        # "Things you didn't know about manufactured homes"
    "financing_tips",      # Affordable living, payment breakdowns
    "before_after",        # Transformation/delivery stories
    "behind_scenes",       # Factory tours, delivery process
    "customer_story",      # Testimonials and success stories
    "comparison",          # Mobile home vs apartment, etc.
    "lifestyle",           # Living in a manufactured home community
    "clearance_alert",     # Red tag / sale promotions
    "faq"                  # Common questions answered
]

# Trending hooks for manufactured home content
VIRAL_HOOKS = [
    "POV: You just realized you can own a home for less than rent 🏠",
    "Things I wish I knew before buying a manufactured home...",
    "Wait for it... this kitchen is UNREAL 🔥",
    "Y'all, stop scrolling. Look at this floor plan!",
    "Apartment prices got you stressed? Let me show you something...",
    "They said mobile homes are ugly. Then I showed them THIS:",
    "3 beds, 2 baths, under $900/month? Here's how 👇",
    "The way my jaw DROPPED when I walked in here...",
    "Everything wrong with apartments (and how I fixed it):",
    "People are doing something CRAZY with housing right now..."
]

# Platform-specific formatting
PLATFORM_SPECS = {
    "tiktok": {
        "max_duration": 60,
        "aspect_ratio": "9:16",
        "hashtag_limit": 5,
        "trending_sounds": True,
        "optimal_length": "15-30 seconds"
    },
    "instagram_reels": {
        "max_duration": 90,
        "aspect_ratio": "9:16",
        "hashtag_limit": 30,
        "trending_sounds": True,
        "optimal_length": "15-30 seconds"
    },
    "facebook": {
        "max_duration": 240,
        "aspect_ratio": "16:9 or 9:16",
        "hashtag_limit": 3,
        "trending_sounds": False,
        "optimal_length": "30-60 seconds"
    }
}


def generate_content_script(
    home_id: Optional[str] = None,
    home_name: Optional[str] = None,
    home_price: Optional[str] = None,
    home_specs: Optional[dict] = None,
    content_theme: str = "home_tour",
    platform: str = "tiktok",
    custom_hook: Optional[str] = None,
    tool_context: ToolContext = None
) -> dict:
    """
    Generate a viral-ready video script for social media.
    
    Args:
        home_id: Inventory ID (e.g., {{BUSINESS_SHORT}}-2024-001)
        home_name: Model name (e.g., "The Nassau")
        home_price: Display price (e.g., "$89,900")
        home_specs: Home specifications {beds, baths, sq_ft}
        content_theme: Type of content to generate
        platform: Target platform (tiktok, instagram_reels, facebook)
        custom_hook: Custom opening hook (optional)
        tool_context: ADK tool context
    
    Returns:
        Dictionary with complete script and posting details
    """
    script_id = f"SCRIPT-{uuid.uuid4().hex[:6].upper()}"
    
    # Select or use custom hook
    hook = custom_hook or random.choice(VIRAL_HOOKS)
    
    # Build script based on theme
    if content_theme == "home_tour" and home_name:
        script = {
            "hook": hook,
            "body": f"""
[SHOT 1: Exterior approach]
*walking up* "Welcome to the {home_name} at {{BUSINESS_NAME}}..."

[SHOT 2: Living room reveal]  
*door opens* "Look at this open floor plan! {home_specs.get('sq_ft', 1200)} square feet of SPACE."

[SHOT 3: Kitchen pan]
"The kitchen? Don't even get me started. This island is PERFECT for hosting."

[SHOT 4: Bedroom]
"{home_specs.get('beds', 3)} bedrooms, {home_specs.get('baths', 2)} baths - and check out this master suite..."

[SHOT 5: Price reveal]
"The best part? This is only {home_price or 'way less than you think'}. 
Stop paying someone else's mortgage. Come see us today!"
""",
            "cta": "Link in bio to schedule your tour! 🏠",
            "duration_estimate": "25-35 seconds"
        }
    elif content_theme == "myth_busting":
        script = {
            "hook": "Things they don't tell you about manufactured homes... 👀",
            "body": """
[SHOT 1: Talking head]
"Everyone thinks mobile homes are cheap and fall apart. Let me show you the TRUTH..."

[SHOT 2: Quality features]
"These are built to HUD code - that's FEDERAL construction standards."
*shows construction details*

[SHOT 3: Comparison]
"Same 3 bed, 2 bath? $2,500/month apartment vs $800/month owning THIS."

[SHOT 4: Tour snippet]
"And look at these finishes... granite, island kitchen, walk-in closets..."

[SHOT 5: CTA]
"Y'all have been lied to. Come see for yourself in {{BUSINESS_CITY}}!"
""",
            "cta": "DM us 'TOUR' for directions! 📍",
            "duration_estimate": "30-45 seconds"
        }
    elif content_theme == "clearance_alert":
        script = {
            "hook": "🚨 CLEARANCE ALERT 🚨 This won't last...",
            "body": f"""
[SHOT 1: Urgency]
"Y'all, we have to move this {home_name or 'beauty'} THIS MONTH."

[SHOT 2: Quick tour]
*speed walkthrough* 
"3 beds, 2 baths, gorgeous kitchen, look at this!"

[SHOT 3: Price drop]
"Red tag price: {home_price or 'SLASHED'}. 
When it's gone, it's GONE."

[SHOT 4: Final push]
"Seriously, I've already had 4 people ask about this one today."
""",
            "cta": "First come, first served. Link in bio! ⬆️",
            "duration_estimate": "20-30 seconds"
        }
    else:
        # Generic template
        script = {
            "hook": hook,
            "body": """
[Customize based on specific content needs]

Key talking points:
- Affordability vs renting
- Quality construction (HUD code)
- Beautiful modern finishes
- Family-owned, no-pressure experience
- Financing available
""",
            "cta": "Come visit us! Link in bio 🏠",
            "duration_estimate": "25-40 seconds"
        }
    
    # Generate hashtags
    base_hashtags = ["#yourbusiness", "#manufacturedhomes", "#affordablehousing"]
    platform_hashtags = {
        "tiktok": ["#fyp", "#hometour", "#housingcrisis", "#mobilehome", "#dreamhome"],
        "instagram_reels": ["#realestate", "#dreamhome", "#househunting", "#newhome", "#homebuying"],
        "facebook": ["#AffordableHomes", "#RealEstate", "#AffordableLiving"]
    }
    
    hashtags = base_hashtags + platform_hashtags.get(platform, [])[:PLATFORM_SPECS[platform]["hashtag_limit"]]
    
    return {
        "success": True,
        "script_id": script_id,
        "platform": platform,
        "content_theme": content_theme,
        "script": script,
        "hashtags": hashtags,
        "platform_specs": PLATFORM_SPECS[platform],
        "home_featured": home_name,
        "created_at": datetime.now().isoformat(),
        "status": "ready_for_production"
    }


def get_trending_content_ideas(
    inventory_highlights: Optional[List[dict]] = None,
    recent_sales: Optional[int] = None,
    current_promotions: Optional[List[str]] = None,
    tool_context: ToolContext = None
) -> dict:
    """
    Generate a batch of trending content ideas based on inventory and promotions.
    
    Args:
        inventory_highlights: List of featured homes to promote
        recent_sales: Number of recent sales for social proof content
        current_promotions: Active promotions (Red Tag, Year End, etc.)
        tool_context: ADK tool context
    
    Returns:
        Dictionary with content calendar ideas
    """
    ideas = []
    
    # Always include these staples
    staple_content = [
        {
            "type": "myth_busting",
            "title": "Mobile Home Myths DEBUNKED",
            "platform_priority": ["tiktok", "instagram_reels"],
            "trending_potential": "high",
            "notes": "These always perform well - people love being proven wrong"
        },
        {
            "type": "home_tour",
            "title": "Full Home Tour - Featured Model",
            "platform_priority": ["tiktok", "instagram_reels", "facebook"],
            "trending_potential": "medium-high",
            "notes": "Tour the best-looking model currently on the lot"
        }
    ]
    ideas.extend(staple_content)
    
    # If there are clearance homes, prioritize urgency content
    if current_promotions:
        for promo in current_promotions[:2]:  # Limit to 2
            ideas.append({
                "type": "clearance_alert",
                "title": f"🚨 {promo} - Limited Time",
                "platform_priority": ["tiktok", "facebook"],
                "trending_potential": "high",
                "notes": "Urgency content drives immediate engagement and DMs"
            })
    
    # Social proof content
    if recent_sales and recent_sales > 0:
        ideas.append({
            "type": "customer_story",
            "title": f"We helped {recent_sales} families this month!",
            "platform_priority": ["facebook", "instagram_reels"],
            "trending_potential": "medium",
            "notes": "Social proof builds trust - ask for customer video testimonials"
        })
    
    # Trending format ideas
    trend_ideas = [
        {
            "type": "comparison",
            "title": "Apartment vs Own This Home (Same Price!) 🤯",
            "platform_priority": ["tiktok"],
            "trending_potential": "very high",
            "notes": "Side-by-side comparisons are VIRAL right now"
        },
        {
            "type": "behind_scenes",
            "title": "Watch This Home Get Delivered 🚚",
            "platform_priority": ["tiktok", "instagram_reels"],
            "trending_potential": "high",
            "notes": "Behind-the-scenes process content performs very well"
        },
        {
            "type": "faq",
            "title": "Answering Your DMs: Top 5 Questions",
            "platform_priority": ["tiktok", "instagram_reels"],
            "trending_potential": "medium",
            "notes": "Builds engagement and answers objections proactively"
        }
    ]
    ideas.extend(trend_ideas)
    
    return {
        "success": True,
        "content_ideas": ideas,
        "recommended_posting_schedule": {
            "tiktok": "1-2x daily for maximum reach",
            "instagram_reels": "1x daily",
            "facebook": "1x daily, boost top performers"
        },
        "top_priority": ideas[0] if ideas else None,
        "generated_at": datetime.now().isoformat()
    }


def schedule_social_post(
    platform: str,
    content_type: str,
    script_id: Optional[str] = None,
    post_time: Optional[str] = None,
    caption: Optional[str] = None,
    hashtags: Optional[List[str]] = None,
    video_url: Optional[str] = None,
    tool_context: ToolContext = None
) -> dict:
    """
    Schedule a post for publishing to social media.
    
    Args:
        platform: Target platform (tiktok, instagram, facebook)
        content_type: Type of content (video, image, carousel)
        script_id: Reference to generated script
        post_time: Scheduled time (ISO format) or "now"
        caption: Post caption/description
        hashtags: Hashtags to include
        video_url: URL to video file
        tool_context: ADK tool context
    
    Returns:
        Dictionary with scheduling confirmation
    """
    post_id = f"POST-{platform.upper()[:2]}-{uuid.uuid4().hex[:6].upper()}"
    
    # Optimal posting times by platform
    optimal_times = {
        "tiktok": ["7:00 AM", "12:00 PM", "7:00 PM", "10:00 PM"],
        "instagram": ["9:00 AM", "12:00 PM", "5:00 PM"],
        "facebook": ["9:00 AM", "1:00 PM", "4:00 PM"]
    }
    
    scheduled_time = post_time or datetime.now().isoformat()
    
    return {
        "success": True,
        "post_id": post_id,
        "platform": platform,
        "content_type": content_type,
        "script_reference": script_id,
        "scheduled_time": scheduled_time,
        "caption": caption,
        "hashtags": hashtags or [],
        "video_url": video_url,
        "status": "scheduled",
        "optimal_times": optimal_times.get(platform, []),
        "tip": f"For {platform}, best engagement is typically at {optimal_times.get(platform, ['varies'])[0]} CST"
    }


def analyze_content_performance(
    post_ids: Optional[List[str]] = None,
    date_range: str = "7d",
    tool_context: ToolContext = None
) -> dict:
    """
    Analyze performance of recent social media content.
    
    Note: In production, this would query actual social media APIs.
    
    Args:
        post_ids: Specific posts to analyze
        date_range: Time period (7d, 30d, etc.)
        tool_context: ADK tool context
    
    Returns:
        Dictionary with performance metrics and recommendations
    """
    # Simulated performance data - would come from APIs in production
    return {
        "success": True,
        "date_range": date_range,
        "summary": {
            "total_views": "15.2K",
            "total_engagement": "2.1K",
            "new_followers": 127,
            "dms_received": 34,
            "leads_generated": 12
        },
        "top_performing_content": [
            {"type": "home_tour", "views": "8.3K", "engagement_rate": "14.2%"},
            {"type": "myth_busting", "views": "4.1K", "engagement_rate": "11.8%"},
            {"type": "clearance_alert", "views": "2.8K", "engagement_rate": "18.5%"}
        ],
        "recommendations": [
            "Home tour content is performing best - increase frequency",
            "Clearance alerts have highest engagement rate - use for time-sensitive promos",
            "Post more during 7-8 PM CST - that's your peak engagement window"
        ],
        "generated_at": datetime.now().isoformat()
    }
