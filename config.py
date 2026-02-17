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

# ---------------------------------------------------------------------------
# Domains — categorised opportunity generation
# ---------------------------------------------------------------------------

DOMAINS = [
    {
        "id": "education_edtech",
        "name": "Education / Courses / EdTech",
        "description": "Online learning platforms, course creation tools, tutoring apps, classroom tech, student productivity, skills training, and educational content delivery.",
        "min_ideas": 3,
        "max_ideas": 5,
        "icon": "\U0001f4da",
    },
    {
        "id": "real_estate_housing",
        "name": "Real Estate / Housing",
        "description": "Property search, tenant/landlord tools, mortgage and financing, home improvement, real estate investing, property management, and housing market analysis.",
        "min_ideas": 3,
        "max_ideas": 5,
        "icon": "\U0001f3e0",
    },
    {
        "id": "government_compliance",
        "name": "Government / Compliance / Policy",
        "description": "Regulatory compliance tools, government services automation, civic tech, policy tracking, grant management, permitting, and public sector software.",
        "min_ideas": 3,
        "max_ideas": 5,
        "icon": "\U0001f3db\ufe0f",
    },
    {
        "id": "food_hospitality",
        "name": "Food / Cooking / Hospitality",
        "description": "Restaurant tech, food delivery, recipe and meal planning, kitchen management, hotel and travel tech, food safety, and culinary content platforms.",
        "min_ideas": 3,
        "max_ideas": 5,
        "icon": "\U0001f373",
    },
    {
        "id": "health_wellness",
        "name": "Health / Wellness / Fitness",
        "description": "Fitness tracking, mental health tools, telehealth, wellness coaching, nutrition, health data, patient engagement, and preventive health apps.",
        "min_ideas": 3,
        "max_ideas": 5,
        "icon": "\U0001f4aa",
    },
    {
        "id": "finance_personal_finance",
        "name": "Finance / Personal Finance",
        "description": "Budgeting apps, investment tools, tax preparation, financial literacy, payment processing, accounting for small business, and fintech infrastructure.",
        "min_ideas": 3,
        "max_ideas": 5,
        "icon": "\U0001f4b0",
    },
    {
        "id": "ecommerce_retail",
        "name": "E-commerce / Retail",
        "description": "Online store tools, product sourcing, inventory management, shipping logistics, marketplace integrations, conversion optimization, and retail analytics.",
        "min_ideas": 3,
        "max_ideas": 5,
        "icon": "\U0001f6d2",
    },
]

DOMAIN_NEWS_SOURCES = {
    "education_edtech": {
        "rss": [
            {"url": "https://www.edsurge.com/articles_rss", "name": "EdSurge"},
            {"url": "https://www.insidehighered.com/rss/feed", "name": "Inside Higher Ed"},
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
            {"url": "https://www.ftc.gov/rss/press-release.xml", "name": "FTC"},
            {"url": "https://www.govtech.com/rss/", "name": "GovTech"},
        ],
        "subreddits": ["govtech"],
    },
    "food_hospitality": {
        "rss": [
            {"url": "https://www.restaurantbusinessonline.com/rss.xml", "name": "Restaurant Business"},
            {"url": "https://www.nrn.com/rss.xml", "name": "Nation's Restaurant News"},
            {"url": "https://skift.com/feed/", "name": "Skift"},
        ],
        "subreddits": ["restaurantowners", "KitchenConfidential"],
    },
    "health_wellness": {
        "rss": [
            {"url": "https://www.statnews.com/feed/", "name": "STAT News"},
            {"url": "https://www.fiercehealthcare.com/rss/xml", "name": "Fierce Healthcare"},
        ],
        "subreddits": ["healthIT", "fitness"],
    },
    "finance_personal_finance": {
        "rss": [
            {"url": "https://www.finextra.com/rss/headlines.aspx", "name": "Finextra"},
            {"url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=21324812", "name": "CNBC Personal Finance"},
        ],
        "subreddits": ["personalfinance", "fintech"],
    },
    "ecommerce_retail": {
        "rss": [
            {"url": "https://www.retaildive.com/feeds/news/", "name": "Retail Dive"},
            {"url": "https://practicalcommerce.com/feed", "name": "Practical Ecommerce"},
        ],
        "subreddits": ["ecommerce", "shopify"],
    },
}
