#!/usr/bin/env python3
"""Compare GLM-5.2 vs DSV4 Flash extraction quality on 5 sessions."""
import json
import os
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from ferrite.extractor import extract


def llm_client_factory(model_name, base_url, api_key):
    def llm_client(system_prompt: str, user_prompt: str) -> str:
        payload = json.dumps({
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 16384,
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
    return llm_client


def get_sessions(db_path, limit=5):
    """Get 5 small-medium sessions suitable for comparison."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Pick sessions with 5-30 messages (not too big, not too small)
    rows = conn.execute(
        """SELECT s.id, s.source, s.model, s.title, s.started_at, s.ended_at,
                  (SELECT count(*) FROM messages m WHERE m.session_id = s.id) as msg_count
           FROM sessions s
           WHERE (SELECT count(*) FROM messages m WHERE m.session_id = s.id) BETWEEN 5 AND 30
           ORDER BY s.started_at
           LIMIT ?""",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_messages(db_path, session_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT role, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp",
        (session_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def main():
    db_path = os.path.expanduser("~/.hermes/state.db")
    base_url = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
    api_key = os.environ.get("LITELLM_API_KEY", "")

    sessions = get_sessions(db_path, 5)
    print(f"Comparing GLM-5.2 vs DSV4-Flash on {len(sessions)} sessions\n")

    glm_client = llm_client_factory("glm-5.2", base_url, api_key)
    dsv4_client = llm_client_factory("deepseek-v4-flash", base_url, api_key)

    results = []
    for i, s in enumerate(sessions):
        sid = s['id']
        title = (s.get('title') or '(untitled)')[:50]
        msgs = get_messages(db_path, sid)
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)

        print(f"[{i+1}/{len(sessions)}] {sid} — {title}")
        print(f"  {len(msgs)} messages, {len(transcript)} chars")

        # GLM
        t0 = time.time()
        glm_result = extract(transcript, glm_client)
        glm_time = time.time() - t0
        glm_facts = glm_result.facts
        print(f"  GLM-5.2:  {len(glm_facts)} facts in {glm_time:.1f}s")

        # DSV4
        t0 = time.time()
        dsv4_result = extract(transcript, dsv4_client)
        dsv4_time = time.time() - t0
        dsv4_facts = dsv4_result.facts
        print(f"  DSV4:     {len(dsv4_facts)} facts in {dsv4_time:.1f}s")

        speed = glm_time / dsv4_time if dsv4_time > 0 else 0
        fact_r = len(dsv4_facts) / len(glm_facts) if glm_facts else 0
        print(f"  Speed: {speed:.1f}x | Fact ratio: {fact_r:.0%}\n")

        results.append({
            'session': sid[:20],
            'title': title,
            'msgs': len(msgs),
            'glm_facts': len(glm_facts),
            'dsv4_facts': len(dsv4_facts),
            'glm_time': glm_time,
            'dsv4_time': dsv4_time,
            'speed': speed,
            'fact_ratio': fact_r,
            'glm_sample': [f"{f.subject} {f.predicate} {f.object}" for f in glm_facts[:3]],
            'dsv4_sample': [f"{f.subject} {f.predicate} {f.object}" for f in dsv4_facts[:3]],
        })

    # Summary table
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for r in results:
        print(f"{r['session']:<22} GLM:{r['glm_facts']:>3} ({r['glm_time']:>5.1f}s)  "
              f"DSV4:{r['dsv4_facts']:>3} ({r['dsv4_time']:>5.1f}s)  "
              f"Speed:{r['speed']:.1f}x  Facts:{r['fact_ratio']:.0%}")

    avg_speed = sum(r['speed'] for r in results) / len(results)
    avg_fact = sum(r['fact_ratio'] for r in results) / len(results)
    print("-" * 80)
    print(f"{'AVG':<22} {'':>16} {'':>16} Speed:{avg_speed:.1f}x  Facts:{avg_fact:.0%}")

    print("\n\nSAMPLE FACTS:")
    for r in results:
        print(f"\n{r['session']} ({r['title']}):")
        print(f"  GLM:  {r['glm_sample']}")
        print(f"  DSV4: {r['dsv4_sample']}")


if __name__ == "__main__":
    main()
