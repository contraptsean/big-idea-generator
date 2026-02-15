# Big Idea Generator

Fetches news from multiple sources, sends them to Claude for business-opportunity analysis, and delivers a formatted digest via email.

## How it works

```
NewsAPI (top-headlines + everything)  ─┐
                                       ├─▶  Claude (analyse)  ─▶  HTML email
Hacker News (top 30 stories)          ─┘
```

1. **Fetch** – pulls articles from NewsAPI (`/v2/top-headlines`, `/v2/everything`) and the Hacker News Firebase API, deduplicates by URL.
2. **Analyse** – sends the combined articles to Claude, which returns structured JSON opportunities (name, one-liner, problem, audience, revenue model, complexity, tech stack, build plan).
3. **Deliver** – renders the opportunities as a styled HTML email with a plain-text fallback and sends it via SMTP/TLS.

## Quickstart

```bash
# Clone and install
git clone <repo-url> && cd opportunity-radar
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys and SMTP credentials

# Run
python main.py              # fetch, analyse, email
python main.py --dry-run    # print to console, skip email
python main.py --save       # also save JSON to digests/
python main.py --dry-run --save  # both
```

## Configuration

All settings are loaded from environment variables (or a `.env` file). See `.env.example` for the full list:

| Variable | Required | Description |
|---|---|---|
| `NEWS_API_KEY` | yes | NewsAPI key ([newsapi.org](https://newsapi.org)) |
| `ANTHROPIC_API_KEY` | yes | Anthropic API key ([console.anthropic.com](https://console.anthropic.com)) |
| `SMTP_USER` | yes | SMTP login / From address |
| `SMTP_PASSWORD` | yes | SMTP password (use an app password for Gmail) |
| `DIGEST_RECIPIENT` | yes | Email address to receive the digest |
| `SMTP_HOST` | no | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_PORT` | no | SMTP port (default: `587`) |
| `NEWS_QUERY` | no | Search query for NewsAPI `/v2/everything` (default: `technology startup funding`) |
| `NEWS_PAGE_SIZE` | no | Articles per NewsAPI request (default: `10`) |

## Web Dashboard

A read-only dashboard for browsing saved digests in the browser.

### Local dev

```bash
# Generate manifest and copy digests into frontend/
python build_manifest.py

# Serve the frontend
python -m http.server 5500 -d frontend
```

Open http://localhost:5500.

You can also run the FastAPI server (`uvicorn api.server:app --reload --port 8000`) if you prefer the API-based workflow locally.

### Netlify deployment

The frontend is a fully static site — no backend server needed.

1. Push the repo to GitHub (make sure `digests/` is not in `.gitignore`).
2. Connect the repo in Netlify. The `netlify.toml` runs `python build_manifest.py` as the build command and publishes `frontend/`.
3. That's it. Each time you push new digests, Netlify rebuilds automatically.

## Project structure

```
opportunity-radar/
  main.py              CLI entry point, wires everything together
  news_fetcher.py      NewsAPI + Hacker News fetchers
  analyzer.py          Claude-powered opportunity analysis
  digest_sender.py     HTML email builder and SMTP sender
  config.py            Loads environment variables
  api/server.py        FastAPI read-only dashboard API (optional, for local dev)
  frontend/index.html  Vue 3 + Bootstrap 5 dashboard (single-file, no build)
  build_manifest.py    Copies digests into frontend/ and generates manifest.json
  netlify.toml         Netlify deployment config
  requirements.txt     Python dependencies
  .env.example         Template for environment variables
  crontab.example      Cron schedule example
  digests/             JSON output when using --save (auto-created)
```

## Scheduling

### Option A: cron (any Linux/macOS server)

A ready-to-use crontab entry is provided in `crontab.example`. It runs the script daily at 7:00 AM:

```bash
# Install directly
crontab -l > /tmp/crontab.bak
cat crontab.example >> /tmp/crontab.bak
crontab /tmp/crontab.bak

# Verify
crontab -l
```

Edit the paths in `crontab.example` to match your installation directory and virtualenv location.

### Option B: AWS Lambda + EventBridge schedule

This is a good fit if you don't want to maintain a server. Lambda's default 15-minute timeout is plenty for this workload.

#### 1. Create a Lambda handler

Add a `lambda_function.py` to the project root:

```python
from main import main


def handler(event, context):
    """AWS Lambda entry point."""
    # --save is omitted because Lambda has an ephemeral filesystem.
    # To persist JSON output, write to S3 instead.
    main([])  # no flags = fetch, analyse, email
    return {"statusCode": 200, "body": "Digest sent"}
```

#### 2. Package the deployment zip

```bash
# Create a fresh build directory
mkdir -p build
pip install -r requirements.txt -t build/

# Copy application code
cp main.py news_fetcher.py analyzer.py digest_sender.py config.py build/

# Zip it up
cd build && zip -r ../deployment.zip . && cd ..
```

#### 3. Create the Lambda function

```bash
aws lambda create-function \
  --function-name opportunity-radar \
  --runtime python3.12 \
  --handler lambda_function.handler \
  --zip-file fileb://deployment.zip \
  --timeout 300 \
  --memory-size 256 \
  --role arn:aws:iam::<ACCOUNT_ID>:role/<LAMBDA_ROLE> \
  --environment "Variables={
    NEWS_API_KEY=...,
    ANTHROPIC_API_KEY=...,
    SMTP_USER=...,
    SMTP_PASSWORD=...,
    DIGEST_RECIPIENT=...
  }"
```

> For production, store secrets in AWS Secrets Manager or SSM Parameter Store
> instead of plain environment variables, and fetch them in `config.py`.

#### 4. Add an EventBridge (CloudWatch) schedule rule

```bash
# Create a rule that fires every day at 7:00 AM UTC
aws events put-rule \
  --name opportunity-radar-daily \
  --schedule-expression "cron(0 7 * * ? *)"

# Allow EventBridge to invoke the Lambda
aws lambda add-permission \
  --function-name opportunity-radar \
  --statement-id eventbridge-daily \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws:events:<REGION>:<ACCOUNT_ID>:rule/opportunity-radar-daily

# Wire the rule to the Lambda
aws events put-targets \
  --rule opportunity-radar-daily \
  --targets "Id"="1","Arn"="arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:opportunity-radar"
```

#### 5. Verify

```bash
# Invoke manually to test
aws lambda invoke --function-name opportunity-radar /dev/stdout

# Check the schedule is active
aws events describe-rule --name opportunity-radar-daily
```

#### Lambda tips

- **Timeout**: set to at least 300 seconds. The Hacker News fetcher makes ~30 sequential HTTP calls and the Claude API call can take 10-20 seconds.
- **Memory**: 256 MB is sufficient. Increase if you raise `NEWS_PAGE_SIZE` significantly.
- **Saving JSON**: Lambda's `/tmp` is ephemeral. To persist digests, swap `_save_json` to write to an S3 bucket instead of local disk.
- **Logs**: Lambda automatically sends stdout/stderr to CloudWatch Logs. All the `logging.info` calls will appear there.
- **python-dotenv**: works fine on Lambda -- it simply won't find a `.env` file and will fall through to the real environment variables set on the function.
