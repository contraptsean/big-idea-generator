from __future__ import annotations

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

# Neon PostgreSQL
NEON_DATABASE_URL: str = os.environ["NEON_DATABASE_URL"]

# Reddit (optional — skipped if not set)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD")

# ---------------------------------------------------------------------------
# Domain icons — per-domain emoji for email pills
# ---------------------------------------------------------------------------

DOMAIN_ICONS: dict[str, str] = {
    "ai_ml": "\U0001f916",           # 🤖
    "dev_tools": "\U0001f6e0\ufe0f",  # 🛠️
    "food_hospitality": "\U0001f373", # 🍳
    "health_wellness": "\U0001f4aa",  # 💪
    "real_estate_housing": "\U0001f3e0",  # 🏠
    "finance_personal_finance": "\U0001f4b0",  # 💰
    "ecommerce_retail": "\U0001f6d2",  # 🛒
    "government_compliance": "\U0001f3db\ufe0f",  # 🏛️
    "education_edtech": "\U0001f4da",  # 📚
}

# ---------------------------------------------------------------------------
# Domain groups — grouped opportunity generation with model assignment
# ---------------------------------------------------------------------------

DOMAIN_GROUPS: list[dict] = [
    {
        "group_id": "tech",
        "group_name": "Tech & Developer Tools",
        "model": "claude-sonnet-4-6",
        "icon": "\U0001f916",  # 🤖
        "min_ideas": 5,
        "max_ideas": 8,
        "domains": [
            {
                "id": "ai_ml",
                "name": "AI / Machine Learning",
                "description": (
                    "AI-powered products, LLM applications, ML infrastructure, "
                    "computer vision tools, AI agents, prompt engineering tools, "
                    "and AI-assisted workflows."
                ),
            },
            {
                "id": "dev_tools",
                "name": "Developer Tools / DevOps",
                "description": (
                    "CLI tools, IDE extensions, CI/CD, monitoring, APIs, SDKs, "
                    "infrastructure automation, code generation, testing frameworks, "
                    "and developer productivity."
                ),
            },
        ],
    },
    {
        "group_id": "lifestyle",
        "group_name": "Lifestyle & Wellness",
        "model": "claude-sonnet-4-6",
        "icon": "\U0001f33f",  # 🌿
        "min_ideas": 6,
        "max_ideas": 10,
        "domains": [
            {
                "id": "food_hospitality",
                "name": "Food / Cooking / Hospitality",
                "description": (
                    "Restaurant tech, food delivery, recipe and meal planning, "
                    "kitchen management, hotel and travel tech, food safety, "
                    "and culinary content platforms."
                ),
            },
            {
                "id": "health_wellness",
                "name": "Health / Wellness / Fitness",
                "description": (
                    "Fitness tracking, mental health tools, telehealth, wellness "
                    "coaching, nutrition, health data, patient engagement, "
                    "and preventive health apps."
                ),
            },
            {
                "id": "real_estate_housing",
                "name": "Real Estate / Housing",
                "description": (
                    "Property search, tenant/landlord tools, mortgage and financing, "
                    "home improvement, real estate investing, property management, "
                    "and housing market analysis."
                ),
            },
        ],
    },
    {
        "group_id": "commerce",
        "group_name": "Commerce & Finance",
        "model": "claude-sonnet-4-6",
        "icon": "\U0001f4bc",  # 💼
        "min_ideas": 5,
        "max_ideas": 8,
        "domains": [
            {
                "id": "finance_personal_finance",
                "name": "Finance / Personal Finance",
                "description": (
                    "Budgeting apps, investment tools, tax preparation, financial "
                    "literacy, payment processing, accounting for small business, "
                    "and fintech infrastructure."
                ),
            },
            {
                "id": "ecommerce_retail",
                "name": "E-commerce / Retail",
                "description": (
                    "Online store tools, product sourcing, inventory management, "
                    "shipping logistics, marketplace integrations, conversion "
                    "optimization, and retail analytics."
                ),
            },
        ],
    },
    {
        "group_id": "civic",
        "group_name": "Civic & Education",
        "model": "claude-sonnet-4-6",
        "icon": "\U0001f3db\ufe0f",  # 🏛️
        "min_ideas": 5,
        "max_ideas": 8,
        "domains": [
            {
                "id": "government_compliance",
                "name": "Government / Compliance / Policy",
                "description": (
                    "Regulatory compliance tools, government services automation, "
                    "civic tech, policy tracking, grant management, permitting, "
                    "and public sector software."
                ),
            },
            {
                "id": "education_edtech",
                "name": "Education / Courses / EdTech",
                "description": (
                    "Online learning platforms, course creation tools, tutoring apps, "
                    "classroom tech, student productivity, skills training, "
                    "and educational content delivery."
                ),
            },
        ],
    },
]

# Backward-compatible flat DOMAINS list (flattened from DOMAIN_GROUPS)
DOMAINS: list[dict] = [
    domain for group in DOMAIN_GROUPS for domain in group["domains"]
]

# ---------------------------------------------------------------------------
# Domain-specific news sources
# ---------------------------------------------------------------------------

DOMAIN_NEWS_SOURCES: dict[str, dict] = {
    "ai_ml": {
        "rss": [
            {
                "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
                "name": "TechCrunch AI",
            },
            {
                "url": "https://www.technologyreview.com/feed/",
                "name": "MIT Tech Review",
            },
        ],
        "subreddits": ["MachineLearning", "artificial"],
    },
    "dev_tools": {
        "rss": [
            {"url": "https://changelog.com/news/feed", "name": "Changelog"},
            {"url": "https://lobste.rs/rss", "name": "Lobsters"},
        ],
        "subreddits": ["devops", "selfhosted"],
    },
    "education_edtech": {
        "rss": [
            {"url": "https://www.edsurge.com/articles_rss", "name": "EdSurge"},
            {
                "url": "https://www.insidehighered.com/rss/feed",
                "name": "Inside Higher Ed",
            },
        ],
        "subreddits": ["edtech", "OnlineCourses"],
    },
    "real_estate_housing": {
        "rss": [
            {"url": "https://www.housingwire.com/feed/", "name": "HousingWire"},
            {"url": "https://www.inman.com/feed/", "name": "Inman News"},
        ],
        "subreddits": ["realestate", "RealEstateInvesting"],
    },
    "government_compliance": {
        "rss": [
            {
                "url": "https://www.ftc.gov/rss/press-release.xml",
                "name": "FTC",
            },
            {"url": "https://www.govtech.com/rss/", "name": "GovTech"},
        ],
        "subreddits": ["govtech"],
    },
    "food_hospitality": {
        "rss": [
            {
                "url": "https://www.restaurantbusinessonline.com/rss.xml",
                "name": "Restaurant Business",
            },
            {
                "url": "https://www.nrn.com/rss.xml",
                "name": "Nation's Restaurant News",
            },
            {"url": "https://skift.com/feed/", "name": "Skift"},
        ],
        "subreddits": ["restaurantowners", "KitchenConfidential"],
    },
    "health_wellness": {
        "rss": [
            {"url": "https://www.statnews.com/feed/", "name": "STAT News"},
            {
                "url": "https://www.fiercehealthcare.com/rss/xml",
                "name": "Fierce Healthcare",
            },
        ],
        "subreddits": ["healthIT", "fitness"],
    },
    "finance_personal_finance": {
        "rss": [
            {
                "url": "https://www.finextra.com/rss/headlines.aspx",
                "name": "Finextra",
            },
            {
                "url": (
                    "https://search.cnbc.com/rs/search/combinedcms/view.xml"
                    "?partnerId=wrss01&id=21324812"
                ),
                "name": "CNBC Personal Finance",
            },
        ],
        "subreddits": ["personalfinance", "fintech"],
    },
    "ecommerce_retail": {
        "rss": [
            {
                "url": "https://www.retaildive.com/feeds/news/",
                "name": "Retail Dive",
            },
            {
                "url": "https://practicalcommerce.com/feed",
                "name": "Practical Ecommerce",
            },
        ],
        "subreddits": ["ecommerce", "shopify"],
    },
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_all_domains() -> list[dict]:
    """Flatten DOMAIN_GROUPS into a simple list of all domain dicts."""
    return [domain for group in DOMAIN_GROUPS for domain in group["domains"]]


def get_all_domain_ids() -> list[str]:
    """Return a flat list of all domain IDs."""
    return [domain["id"] for group in DOMAIN_GROUPS for domain in group["domains"]]


def get_group_for_domain(domain_id: str) -> dict | None:
    """Return the parent group dict for a given domain ID, or None."""
    for group in DOMAIN_GROUPS:
        for domain in group["domains"]:
            if domain["id"] == domain_id:
                return group
    return None


def get_group_by_id(group_id: str) -> dict | None:
    """Return a group dict by group_id, or None."""
    for group in DOMAIN_GROUPS:
        if group["group_id"] == group_id:
            return group
    return None
