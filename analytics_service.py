from datetime import datetime, timedelta
import random

def get_analytics_summary():
    """
    Returns aggregated analytics metrics.
    Currently returns mock data for demonstration.
    In production, this would query BigQuery or aggregated log storage.
    """
    
    # Mock data ranges
    today = datetime.now()
    
    # Generate last 7 days trend
    daily_sessions = []
    for i in range(7):
        day = today - timedelta(days=6-i)
        daily_sessions.append({
            "date": day.strftime("%Y-%m-%d"),
            "sessions": random.randint(15, 45),
            "leads": random.randint(1, 5)
        })
        
    return {
        "overview": {
            "total_sessions": 1243,
            "avg_duration": "4m 12s",
            "search_count": 892,
            "lead_conversion_rate": "3.8%"
        },
        "top_searches": [
            {"query": "3 bedroom double wide", "count": 145},
            {"query": "land home packages", "count": 98},
            {"query": "under $100k", "count": 76},
            {"query": "The Nassau", "count": 42},
            {"query": "financing options", "count": 35}
        ],
        "daily_activity": daily_sessions,
        "recent_leads": [
            {"name": "John Doe", "interest": "Double Wide", "time": "2 mins ago"},
            {"name": "Sarah Smith", "interest": "Financing", "time": "1 hour ago"},
            {"name": "Mike Johnson", "interest": "3 Bed/2 Bath", "time": "3 hours ago"}
        ]
    }
