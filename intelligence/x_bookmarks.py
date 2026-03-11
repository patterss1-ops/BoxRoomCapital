"""X (Twitter) API v2 client for fetching bookmarks and likes.

Used by XFeedService to capture the user's curated social signal
(bookmarked/liked tweets) as investment intelligence input.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.twitter.com/2/"
_TWEET_FIELDS = "created_at,author_id,text"
_USER_FIELDS = "username"
_EXPANSIONS = "author_id"


class XBookmarksClient:
    """X API v2 client for fetching bookmarks and likes."""

    def __init__(self, bearer_token: str, user_id: str = ""):
        if not bearer_token:
            raise ValueError("bearer_token is required")
        self.bearer_token = bearer_token
        self._user_id = user_id
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self.bearer_token}",
        })

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def user_id(self) -> str:
        """Lazily resolve user ID from /2/users/me if not provided."""
        if not self._user_id:
            self._user_id = self._get_user_id()
        return self._user_id

    def fetch_bookmarks(
        self,
        since_id: Optional[str] = None,
        max_results: int = 20,
    ) -> list[dict]:
        """GET /2/users/:id/bookmarks - requires OAuth 2.0 User Context.

        Returns list of {'id': str, 'text': str, 'author': str, 'created_at': str}.

        Note: The bookmarks endpoint requires OAuth 2.0 PKCE (user context auth).
        A simple bearer token will return 403.  For now this method is provided
        for future use once PKCE flow is implemented; use ``fetch_likes`` with
        app-level bearer tokens instead.
        """
        params: dict = {
            "tweet.fields": _TWEET_FIELDS,
            "expansions": _EXPANSIONS,
            "user.fields": _USER_FIELDS,
            "max_results": min(max(1, max_results), 100),
        }
        if since_id:
            params["since_id"] = since_id

        url = f"{_BASE_URL}users/{self.user_id}/bookmarks"
        resp = self._session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return self._parse_tweets(resp.json())

    def fetch_likes(
        self,
        since_id: Optional[str] = None,
        max_results: int = 20,
    ) -> list[dict]:
        """GET /2/users/:id/liked_tweets - bearer token works.

        Returns list of {'id': str, 'text': str, 'author': str, 'created_at': str}.
        """
        params: dict = {
            "tweet.fields": _TWEET_FIELDS,
            "expansions": _EXPANSIONS,
            "user.fields": _USER_FIELDS,
            "max_results": min(max(1, max_results), 100),
        }
        if since_id:
            params["since_id"] = since_id

        url = f"{_BASE_URL}users/{self.user_id}/liked_tweets"
        resp = self._session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return self._parse_tweets(resp.json())

    # ── Internal helpers ───────────────────────────────────────────────────

    def _get_user_id(self) -> str:
        """GET /2/users/me to resolve user ID from bearer token."""
        url = f"{_BASE_URL}users/me"
        resp = self._session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        uid = data.get("id", "")
        if not uid:
            raise RuntimeError("Could not resolve user ID from /2/users/me")
        logger.info("Resolved X user ID: %s (username: %s)", uid, data.get("username"))
        return uid

    @staticmethod
    def _parse_tweets(payload: dict) -> list[dict]:
        """Normalise the v2 response into a flat list of tweet dicts.

        Maps author_id -> username via the ``includes.users`` expansion.
        """
        data = payload.get("data")
        if not data:
            return []

        # Build author_id -> username lookup from includes
        users_map: dict[str, str] = {}
        includes = payload.get("includes", {})
        for user in includes.get("users", []):
            users_map[user["id"]] = user.get("username", user["id"])

        tweets: list[dict] = []
        for tweet in data:
            author_id = tweet.get("author_id", "")
            tweets.append({
                "id": tweet["id"],
                "text": tweet.get("text", ""),
                "author": users_map.get(author_id, author_id),
                "created_at": tweet.get("created_at", ""),
            })
        return tweets
