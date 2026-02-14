import os
from dotenv import load_dotenv

load_dotenv()


# News API
NEWS_API_KEY = os.environ["NEWS_API_KEY"]
NEWS_QUERY = os.getenv("NEWS_QUERY", "technology startup funding")
NEWS_PAGE_SIZE = int(os.getenv("NEWS_PAGE_SIZE", "10"))

# Anthropic
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# SMTP / Email
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
DIGEST_RECIPIENT = os.environ["DIGEST_RECIPIENT"]

# Reddit (optional — skipped if not set)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")
