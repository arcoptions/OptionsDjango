"""Attributed public BeatTheStreet text/captions, never verified sentiment."""

from datetime import datetime, timedelta
from html.parser import HTMLParser
import json

from django.db import transaction
from django.utils import timezone
import requests

from .models import ChatMessage


SOURCE = "https://t.me/s/BeatTheStreetnews"
SOURCE_NAME = "BeatTheStreet News"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class PublicNewsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.post = None
        self.post_depth = None
        self.text_depth = None
        self.posts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in VOID:
            self.depth += 1
        identity = attrs.get("data-post", "")
        if identity.startswith("BeatTheStreetnews/"):
            self.post = {"id": int(identity.rsplit("/", 1)[1]), "text": [], "published": None}
            self.post_depth = self.depth
        if self.post is not None:
            if "tgme_widget_message_text" in attrs.get("class", "").split():
                self.text_depth = self.depth
            if tag == "time" and attrs.get("datetime"):
                self.post["published"] = attrs["datetime"]
            if tag == "br" and self.text_depth is not None:
                self.post["text"].append("\n")

    def handle_data(self, data):
        if self.post is not None and self.text_depth is not None:
            self.post["text"].append(data)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.text_depth == self.depth:
            self.text_depth = None
        if self.post is not None and self.post_depth == self.depth:
            self.posts.append(self.post)
            self.post, self.post_depth, self.text_depth = None, None, None
        self.depth -= 1


def collect_public_news(now):
    try:
        response = requests.get(SOURCE, timeout=20)
        response.raise_for_status()
    except requests.RequestException as error:
        raise ValueError(f"Public BeatTheStreet fetch failed ({type(error).__name__}); source is not current.") from None
    parser = PublicNewsParser()
    parser.feed(response.text)
    if not parser.posts:
        raise ValueError("No recognizable public Telegram messages; source markup/access changed.")
    inserted, skipped = 0, 0
    with transaction.atomic():
        for post in parser.posts:
            if not post["published"]:
                raise ValueError("Public Telegram message missing publication timestamp.")
            published = datetime.fromisoformat(post["published"])
            if timezone.is_naive(published):
                raise ValueError("Public message timestamp must be timezone aware.")
            text = "".join(post["text"]).strip()
            if not text or not now-timedelta(days=1) <= published <= now:
                skipped += 1
                continue
            if ChatMessage.objects.filter(source_name=SOURCE_NAME, telegram_message_id=post["id"]).exists():
                skipped += 1
                continue
            ChatMessage.objects.create(
                source_name=SOURCE_NAME, source_category="NEWS", telegram_message_id=post["id"],
                telegram_message_at=published, raw_text=text, normalized_text=" ".join(text.split()),
                raw_payload=json.dumps({"mode": "PUBLIC_TEXT_CAPTION_ONLY", "source_url": f"https://t.me/BeatTheStreetnews/{post['id']}"}),
                is_tip_candidate=False,
            )
            inserted += 1
    return {"inserted": inserted, "skipped": skipped, "public_page_posts": len(parser.posts),
            "coverage": "Latest public text/captions only; not complete channel history, private posts, image OCR or verified catalysts."}
