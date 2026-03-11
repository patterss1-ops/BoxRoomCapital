"""Shared bookmarklet and X/Twitter ingestion helpers."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable


def _build_bookmarklet_href(js_source: str, endpoint: str, *, re_module: Any) -> str:
    """Build a safe bookmarklet href without corrupting embedded URLs."""
    src = js_source.replace("%%ENDPOINT%%", endpoint)
    src = re_module.sub(r"/\*.*?\*/", "", src, flags=re_module.DOTALL)
    src = re_module.sub(r"(?m)^\s*//.*$", "", src)
    src = re_module.sub(r"\s+", " ", src).strip()
    return "javascript:" + src


def _parse_debate_parts(debate_summary: str, *, re_module: Any) -> list[dict[str, str]]:
    """Parse the debate summary into per-model parts for display."""
    if not debate_summary:
        return []
    parts = re_module.split(r"\[(\w+)\]\s*", debate_summary)
    result = []
    i = 1
    while i < len(parts) - 1:
        result.append({"model": parts[i], "text": parts[i + 1].strip()})
        i += 2
    if not result and debate_summary:
        result.append({"model": "council", "text": debate_summary})
    return result


def _extract_bookmarklet_version(js_source: str, *, re_module: Any) -> str:
    """Extract the inline bookmarklet version stamp when present."""
    match = re_module.search(r'BOOKMARKLET_VERSION\s*=\s*"([^"]+)"', js_source)
    if not match:
        return "unknown"
    return match.group(1).strip() or "unknown"


def _get_x_oauth(
    *,
    config_module: Any,
    logger: Any,
    env_path: Path,
) -> Any:
    """Create an authenticated X API OAuth1 session, or None if unconfigured."""
    ck = config_module.X_CONSUMER_KEY
    cs = config_module.X_CONSUMER_SECRET
    at = config_module.X_ACCESS_TOKEN
    ats = config_module.X_ACCESS_TOKEN_SECRET
    if not all([ck, cs, at, ats]):
        from dotenv import load_dotenv

        load_dotenv(env_path, override=True)
        ck = os.getenv("X_CONSUMER_KEY", "")
        cs = os.getenv("X_CONSUMER_SECRET", "")
        at = os.getenv("X_ACCESS_TOKEN", "")
        ats = os.getenv("X_ACCESS_TOKEN_SECRET", "")
        config_module.X_CONSUMER_KEY = ck
        config_module.X_CONSUMER_SECRET = cs
        config_module.X_ACCESS_TOKEN = at
        config_module.X_ACCESS_TOKEN_SECRET = ats
    if not all([ck, cs, at, ats]):
        logger.warning("X API credentials not configured (checked .env and env vars)")
        return None
    from requests_oauthlib import OAuth1Session

    return OAuth1Session(ck, client_secret=cs, resource_owner_key=at, resource_owner_secret=ats)


def _fetch_single_tweet(oauth: Any, tweet_id: str, *, logger: Any) -> dict[str, Any] | None:
    """Fetch a single tweet with full metadata."""
    resp = oauth.get(
        f"https://api.x.com/2/tweets/{tweet_id}",
        params={
            "tweet.fields": "text,author_id,created_at,conversation_id,referenced_tweets,note_tweet,attachments",
            "expansions": "author_id,attachments.media_keys,referenced_tweets.id",
            "media.fields": "type,url,alt_text",
            "user.fields": "username,name",
        },
        timeout=10,
    )
    if resp.status_code != 200:
        logger.warning("X API returned %d: %s", resp.status_code, resp.text[:200])
        return None
    return resp.json()


def _resolve_author(data: dict[str, Any], author_id: str) -> str:
    """Extract username from includes.users."""
    for user in data.get("includes", {}).get("users", []):
        if user.get("id") == author_id:
            return user.get("username", "")
    return ""


def _get_tweet_text(data: dict[str, Any]) -> str:
    """Get full tweet text, preferring note_tweet over regular text."""
    tweet = data.get("data", {})
    note = tweet.get("note_tweet", {})
    if note and note.get("text"):
        return note["text"]
    return tweet.get("text", "")


def _describe_media(data: dict[str, Any]) -> str:
    """Summarize attached media from includes."""
    media_list = data.get("includes", {}).get("media", [])
    if not media_list:
        return ""
    descriptions = []
    for media in media_list:
        media_type = media.get("type", "unknown")
        alt = media.get("alt_text", "")
        if alt:
            descriptions.append(f"[{media_type}: {alt}]")
        else:
            descriptions.append(f"[{media_type} attached]")
    return "\n".join(descriptions)


def _fetch_thread(
    oauth: Any,
    conversation_id: str,
    author_username: str,
    *,
    logger: Any,
) -> list[str]:
    """Fetch all tweets in a thread by the same author (recent threads only)."""
    try:
        resp = oauth.get(
            "https://api.x.com/2/tweets/search/recent",
            params={
                "query": f"conversation_id:{conversation_id} from:{author_username}",
                "tweet.fields": "text,created_at,note_tweet",
                "max_results": 100,
                "sort_order": "recency",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        tweets = resp.json().get("data", [])
        tweets.reverse()
        parts = []
        for tweet in tweets:
            note = tweet.get("note_tweet", {})
            text = note.get("text") if note and note.get("text") else tweet.get("text", "")
            parts.append(text)
        return parts
    except Exception as exc:
        logger.warning("Thread fetch failed: %s", exc)
        return []


def _fetch_tweet_from_url(
    url: str,
    *,
    get_x_oauth: Callable[[], Any],
    fetch_single_tweet: Callable[[Any, str], dict[str, Any] | None],
    resolve_author: Callable[[dict[str, Any], str], str],
    get_tweet_text: Callable[[dict[str, Any]], str],
    describe_media: Callable[[dict[str, Any]], str],
    fetch_thread: Callable[[Any, str, str], list[str]],
    logger: Any,
    re_module: Any,
) -> dict[str, Any] | None:
    """Fetch full tweet text from an X/Twitter URL using the v2 API."""
    match = re_module.search(r"(?:twitter\.com|x\.com)/.+/status/(\d+)", url)
    if not match:
        return None

    tweet_id = match.group(1)
    oauth = get_x_oauth()
    if not oauth:
        return None

    try:
        data = fetch_single_tweet(oauth, tweet_id)
        if not data or "data" not in data:
            return None

        tweet = data["data"]
        author_id = tweet.get("author_id", "")
        author = resolve_author(data, author_id)
        created_at = tweet.get("created_at", "")
        conversation_id = tweet.get("conversation_id", "")

        retweeted_id = None
        for ref in tweet.get("referenced_tweets", []):
            if ref.get("type") == "retweeted":
                retweeted_id = ref.get("id")
                break

        if retweeted_id:
            orig_data = fetch_single_tweet(oauth, retweeted_id)
            if orig_data and "data" in orig_data:
                orig_tweet = orig_data["data"]
                orig_author = resolve_author(orig_data, orig_tweet.get("author_id", ""))
                text = get_tweet_text(orig_data)
                media_desc = describe_media(orig_data)
                if media_desc:
                    text += f"\n\n{media_desc}"
                orig_conv_id = orig_tweet.get("conversation_id", "")
                if orig_conv_id and orig_author:
                    thread_parts = fetch_thread(oauth, orig_conv_id, orig_author)
                    if len(thread_parts) > 1:
                        text = "\n\n---\n\n".join(thread_parts)
                        if media_desc:
                            text += f"\n\n{media_desc}"
                return {
                    "text": f"RT @{orig_author}: {text}" if orig_author else text,
                    "author": author,
                    "created_at": created_at,
                    "tweet_id": tweet_id,
                }

        text = get_tweet_text(data)
        media_desc = describe_media(data)
        if media_desc:
            text += f"\n\n{media_desc}"

        if conversation_id and author:
            thread_parts = fetch_thread(oauth, conversation_id, author)
            if len(thread_parts) > 1:
                text = "\n\n---\n\n".join(thread_parts)
                if media_desc:
                    text += f"\n\n{media_desc}"

        return {
            "text": text,
            "author": author,
            "created_at": created_at,
            "tweet_id": tweet_id,
        }
    except Exception as exc:
        logger.warning("Failed to fetch tweet %s: %s", tweet_id, exc)
        return None
