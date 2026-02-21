"""
Advanced Edge Case Testing for {{BUSINESS_SHORT}} AI Agent
Tests system behavior under unusual conditions
"""

import asyncio
import aiohttp
import json
import time

API_URL = "https://{{BUSINESS_SHORT}}-ai-agent-suy7mgxwyq-uc.a.run.app/run"

async def send_message(session, user_id, session_id, message, test_name):
    """Send a message and return response"""
    try:
        start = time.time()
        async with session.post(API_URL, json={
            "userId": user_id,
            "sessionId": session_id,
            "newMessage": {"parts": [{"text": message}]}
        }, timeout=aiohttp.ClientTimeout(total=30)) as response:
            duration = time.time() - start
            data = await response.json()
            return {
                "test": test_name,
                "status": "success",
                "duration": round(duration, 2),
                "response_length": len(data.get("text", ""))
            }
    except Exception as e:
        return {
            "test": test_name,
            "status": "failed",
            "error": str(e)
        }

async def test_edge_cases():
    """Run comprehensive edge case tests"""
    print("=" * 80)
    print("ADVANCED EDGE CASE TESTING")
    print("=" * 80)
    
    results = []
    
    async with aiohttp.ClientSession() as session:
        # Test 1: Empty inventory query (no matches)
        print("\n[TEST 1] Empty Inventory Query (No Matches)")
        result = await send_message(
            session, "edge_user_1", "edge_session_1",
            "Show me 50 bedroom homes",
            "Empty Inventory Query"
        )
        print(f"  Result: {result['status']} ({result.get('duration', 'N/A')}s)")
        results.append(result)
        
        # Test 2: Long conversation (multiple messages in sequence)
        print("\n[TEST 2] Long Conversation (10 messages)")
        session_id = "edge_session_long"
        conversation_messages = [
            "Hello",
            "Show me homes",
            "What about 3 bedroom?",
            "Tell me about financing",
            "What's your address?",
            "Business hours?",
            "Tell me about the Bourbon model",
            "What's the price?",
            "Can I schedule a showing?",
            "Thanks!"
        ]
        
        for idx, msg in enumerate(conversation_messages, 1):
            result = await send_message(
                session, "edge_user_long", session_id,
                msg,
                f"Long Conversation Msg {idx}"
            )
            print(f"  Message {idx}/{len(conversation_messages)}: {result['status']}")
            results.append(result)
            await asyncio.sleep(0.5)  # Small delay between messages
        
        # Test 3: Very long input
        print("\n[TEST 3] Very Long Input")
        long_message = "Tell me about homes. " * 100  # 2100 chars
        result = await send_message(
            session, "edge_user_3", "edge_session_3",
            long_message,
            "Very Long Input"
        )
        print(f"  Result: {result['status']} ({result.get('duration', 'N/A')}s)")
        results.append(result)
        
        # Test 4: Special characters and emojis
        print("\n[TEST 4] Special Characters")
        result = await send_message(
            session, "edge_user_4", "edge_session_4",
            "Show me homes 🏠💰 with 3-4 bedrooms & <$100k!",
            "Special Characters"
        )
        print(f"  Result: {result['status']} ({result.get('duration', 'N/A')}s)")
        results.append(result)
        
        # Test 5: Rapid fire (stress test)
        print("\n[TEST 5] Rapid Fire (5 simultaneous requests)")
        tasks = []
        for i in range(5):
            task = send_message(
                session, f"rapid_user_{i}", f"rapid_session_{i}",
                "Show me available homes",
                f"Rapid Fire {i+1}"
            )
            tasks.append(task)
        
        rapid_results = await asyncio.gather(*tasks)
        for r in rapid_results:
            print(f"  {r['test']}: {r['status']}")
            results.append(r)
    
    # Summary
    print("\n" + "=" * 80)
    print("EDGE CASE TEST SUMMARY")
    print("=" * 80)
    successful = sum(1 for r in results if r['status'] == 'success')
    failed = sum(1 for r in results if r['status'] == 'failed')
    
    print(f"Total Tests: {len(results)}")
    print(f"✅ Successful: {successful}")
    print(f"❌ Failed: {failed}")
    
    if failed > 0:
        print("\nFailed Tests:")
        for r in results:
            if r['status'] == 'failed':
                print(f"  - {r['test']}: {r.get('error', 'Unknown error')}")
    
    avg_duration = sum(r.get('duration', 0) for r in results if 'duration' in r) / len([r for r in results if 'duration' in r])
    print(f"\nAverage Response Time: {avg_duration:.2f}s")
    
    return results

if __name__ == "__main__":
    asyncio.run(test_edge_cases())
