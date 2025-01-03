#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# MIT License

# Copyright (c) 2017 Matt Doyle

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import datetime
import http.client
import itertools
import langdetect
import logging
import platform
import requests
import string
import sys
import time
import traceback
import urllib.parse


def WordForSize(items_or_count, singular, plural):
    item_count = (
        len(items_or_count) if hasattr(items_or_count, "__len__") else items_or_count
    )
    return singular if item_count == 1 else plural


class NewsBlurClient(object):
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.feeds = None

    def __enter__(self):

        # Create a new session
        self.session = requests.Session()
        self.session.headers = {
            "Accept": "application/json",
            "User-Agent": "newsblurcleaner/1.0.0",
        }

        # Attempt to login
        self.Post(
            "/api/login", data={"username": self.username, "password": self.password}
        )
        print("Successfully authenticated")
        return self

    def __exit__(self, unused_ex_type, unused_ex_value, unused_ex_tb):
        self.session.close()

    def Request(self, method, path, params=None, data=None):

        url = urllib.parse.urljoin("https://www.newsblur.com/", path)
        response = self.session.request(method, url, params=params, data=data)

        if response.status_code != http.client.OK:
            raise Exception(f"{method} to {path} returned {response.status_code}")

        result = response.json()["result"]
        if result != "ok":
            raise Exception(f"{method} to {path} returned {result}")

        return response

    def Post(self, path, data=None):
        return self.Request("POST", path, data=data)

    def Get(self, path, params=None):
        return self.Request("GET", path, params=params)

    def GetFeeds(self):

        if not self.feeds:
            # update_counts: forces recalculation of unread counts on all feeds.
            response = self.Get("/reader/feeds", params={"update_counts": True})
            items = list(response.json()["feeds"].items())
            self.feeds = sorted(
                [Feed(self, feed_id, feed_data) for feed_id, feed_data in items],
                key=lambda f: f.title,
            )

        return self.feeds

    def MarkStoriesAsRead(self, stories):
        data = {"story_hash": [story.hash for story in stories]}
        self.Post("/reader/mark_story_hashes_as_read", data=data)


class Feed(object):
    def __init__(self, client, feed_id, feed_data):

        self.client = client
        self.feed_id = feed_id
        self.feed_data = feed_data or {}

        self.stories = []

    @property
    def title(self):
        return self.feed_data.get("feed_title", self.feed_id)

    @property
    def unread_count(self):
        return self.feed_data.get("nt", 0)

    def GetStories(
        self, page=1, oldest_first=True, unread_only=True, metadata_only=True
    ):

        path = f"/reader/feed/{self.feed_id}"
        params = {
            "page": page,
            "order": "oldest" if oldest_first else "newest",
            "read_filter": "unread" if unread_only else "all",
            "include_story_content": "false" if metadata_only else "true",
        }

        response = self.client.Get(path, params=params)
        items = response.json()["stories"]

        new_stories = [Story(self.client, item) for item in items]
        if unread_only:
            new_stories = [s for s in new_stories if s.unread]
        self.stories.extend(new_stories)

        return new_stories


class Story(object):
    def __init__(self, client, story_data):

        self.client = client
        self.story_id = story_data["id"]
        self.story_data = story_data or {}

    @property
    def content(self):
        return self.story_data.get("story_content", None)

    @property
    def feed_id(self):
        return self.story_data.get("story_feed_id", None)

    @property
    def hash(self):
        return self.story_data.get("story_hash", None)

    @property
    def permalink(self):
        return self.story_data.get("story_permalink", None)

    @property
    def title(self):
        return self.story_data.get("story_title", self.story_id)

    @property
    def unread(self):
        return not self.story_data.get("read_status", 0)

    @property
    def timestamp(self):
        story_timestamp = self.story_data.get("story_timestamp", None)
        if story_timestamp:
            story_timestamp = datetime.datetime.fromtimestamp(
                float(story_timestamp), tz=datetime.timezone.utc
            )
        return (
            story_timestamp
            if story_timestamp
            else datetime.datetime.now(datetime.timezone.utc)
        )

    def NormalizeTitle(self):
        norm_title = self.title.lower()
        norm_title = norm_title.translate(str.maketrans("", "", string.punctuation))
        return norm_title

    def GetLanguage(self):
        return langdetect.detect(self.title)


def main():

    # Parse the command line arguments into a context.
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", dest="username", required=True, type=str)
    parser.add_argument("--password", dest="password", required=True, type=str)
    parser.add_argument("--deduplicate", dest="deduplicate", action="store_true")
    parser.add_argument("--max_days_old", dest="max_days_old", type=int)
    parser.add_argument("--max_stories_per_feed", dest="max_stories_per_feed", type=int)
    parser.add_argument("--language", dest="language", action="append", type=str)
    ctx = parser.parse_args()

    # Login to NewsBlur, and perform any of the specified cleanups.
    with NewsBlurClient(ctx.username, ctx.password) as client:

        # Load all the feeds
        feeds = [f for f in client.GetFeeds() if f.unread_count > 0]
        word = WordForSize(feeds, "feed", "feeds")
        print(f"Retrieved {len(feeds)} {word} with unread stories")

        titles_seen = set()
        permalinks_seen = set()
        all_stories_to_mark = []

        # Calculate the cutoff date if needed.
        cutoff = None
        if ctx.max_days_old:
            now = datetime.datetime.now(datetime.timezone.utc)
            cutoff = now - datetime.timedelta(days=ctx.max_days_old)

        # Build a set of desired languages if needed.
        languages = set(ctx.language if ctx.language else [])

        for feed in feeds:

            print(f"Processing {feed.title}")

            feed_stories = []
            feed_stories_to_mark = []
            page = 1

            # Load stories from the feed page by page.
            word = WordForSize(feed.unread_count, "story", "stories")
            print(f"  Examining {feed.unread_count} {word}")
            while len(feed_stories) < feed.unread_count:
                new_stories = feed.GetStories(page=page, oldest_first=False)
                feed_stories.extend(new_stories)
                page += 1

            # Check each story to see if it should be purged
            for story_num, story in enumerate(feed_stories):

                # Deduplicate, if specified by argument.
                if ctx.deduplicate:

                    # Purge if the exact title as already been seen.
                    norm_title = story.NormalizeTitle()
                    if norm_title in titles_seen:
                        feed_stories_to_mark.append(story)
                        continue

                    # Purge if the exact permalink has already been seen.
                    if story.permalink in permalinks_seen:
                        feed_stories_to_mark.append(story)
                        continue

                # Purge if the story is earlier than the timestamp cutoff
                # specified by argument.
                if (
                    ctx.max_days_old
                    and ctx.max_days_old > 0
                    and story.timestamp < cutoff
                ):
                    feed_stories_to_mark.append(story)
                    continue

                # Purge if there's a max story limit specified by argument, and
                # it has been exceeded.
                if (
                    ctx.max_stories_per_feed
                    and ctx.max_stories_per_feed > 0
                    and story_num >= ctx.max_stories_per_feed
                ):
                    feed_stories_to_mark.append(story)
                    continue

                # Purge any stories which don't match the specified languages,
                # if any.
                if languages and not story.GetLanguage() in languages:
                    feed_stories_to_mark.append(story)
                    continue

                # Keeping the story, so note that it's been seen.
                if ctx.deduplicate:
                    titles_seen.add(norm_title)
                    permalinks_seen.add(story.permalink)

            # Print the per-feed results.
            if feed_stories_to_mark:
                word = WordForSize(all_stories_to_mark, "story", "stories")
                print(f"  Found {len(feed_stories_to_mark)} {word} to mark as read")
                all_stories_to_mark.extend(feed_stories_to_mark)

        # Mark stories as read, if needed.
        if all_stories_to_mark:
            word = WordForSize(all_stories_to_mark, "story", "stories")
            print(f"Marking {len(all_stories_to_mark)} {word} as read")
            client.MarkStoriesAsRead(all_stories_to_mark)
        else:
            print(f"No stories to be marked as read")
            
        print("Done") 


if __name__ == "__main__":
    main()
