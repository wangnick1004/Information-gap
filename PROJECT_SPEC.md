PROJECT SPECIFICATION: LINE Price-Comparison Bot for Anime Goods
1. Executive Summary & Problem Statement
Problem: Taiwan's secondary anime merchandise market (primarily on Facebook transaction groups) suffers from severe information asymmetry. Resellers purchase items from Japanese C2C marketplaces (e.g., Mercari via proxy services like Letao/Buyee) and resell them in Taiwan with excessive markups (+50% to +200%). Buyers lack a frictionless way to cross-check true Japanese market prices.
Solution: A zero-installation LINE Messaging Bot. Users share a Facebook post URL directly to the LINE Bot via their mobile share sheet. The backend extracts post content, uses an LLM to identify the item and translate search terms to Japanese, scrapes Japanese proxy listings (Buyee/Bibian), calculates the landed cost (TWD), and replies with a rich LINE Flex Message containing price comparisons and affiliate purchasing links.
Business Model: Near-zero operational overhead using serverless architecture and free-tier LLMs. Monetization is driven by Affiliate Marketing commissions (CPA/CPS) embedded in the call-to-action buttons.
2. Technical Stack & Architecture
Layer	Technology	Purpose / Notes
Backend Framework	Python (FastAPI)	Lightweight, async-native, fast execution for Webhook handling.
Deployment / Runtime	Netlify Serverless Functions (or AWS Lambda / Mangum)	Stateless, serverless deployment with zero fixed monthly server costs.
Messaging Channel	LINE Messaging API	Webhook receivers + LINE Flex Message UI generation.
NLP & Translation	Gemini 1.5 Flash API	Extracts anime title, character, and merch type from noisy slang-heavy text; converts to Japanese auction search terms.
Scraping / Proxy Query	Requests + BeautifulSoup4 / Playwright	Fetches active and sold listings from proxy platforms (Buyee/Bibian).
Data Validation	Pydantic v2	Request validation, payload typing, and structured outputs.
3. System Architecture & End-to-End Flow
[Mobile User (FB App)] 
       │ (Share Post URL via Share Sheet)
       ▼
[LINE Messaging Platform] 
       │ (HTTP POST Webhook)
       ▼
[FastAPI on Serverless Runtime (/api/webhook)]
       ├── Step 1: Signature Verification (X-Line-Signature)
       ├── Step 2: Extract text & fetch FB post content (Fallback to user text if private)
       ├── Step 3: LLM Parsing (Gemini 1.5 Flash: Extract entity -> Translate to JA keywords)
       ├── Step 4: Scrape Japanese Proxy (Query Buyee/Bibian API or search HTML)
       ├── Step 5: Compute Landed Cost (JPY * FX Rate + Service Fee + Est. Shipping)
       └── Step 6: Construct LINE Flex Message (Price badge + Affiliate CTA link)
       │ (Reply Message API)
       ▼
[User LINE Chat Screen]
4. Phased Implementation Plan (Agent Task Directives)
Phase 1: Core Setup & Webhook Boilerplate
Goal: Establish the project directory structure, dependencies, and a functional LINE Webhook receiver.
Deliverables:
requirements.txt with fastapi, uvicorn, line-bot-sdk, pydantic, python-dotenv, mangum, google-genai.
netlify.toml configuring Python serverless functions.
.env.example defining LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, GEMINI_API_KEY, AFFILIATE_TAG.
main.py implementing /api/webhook with signature verification and an echo reply handler.
Phase 2: NLP Entity Extraction & Japanese Search Keyword Generator
Goal: Convert noisy traditional Chinese trading posts into clean Japanese marketplace search queries.
Deliverables:
services/parser.py: Calls Gemini 1.5 Flash using structured output (JSON schema).
Prompt design: Accepts raw FB text (e.g., 售 排少 影山 2020 趴娃 綁1 1500) and returns:
JSON
{
  "franchise": "ハイキュー!!",
  "character": "影山飛雄",
  "item_type": "もちもちマスコット",
  "year_or_edition": "2020",
  "search_query_ja": "ハイキュー 影山 もちもちマスコット 2020",
  "fb_price_twd": 1500
}
Phase 3: Japanese Proxy Scraper & Cost Engine
Goal: Retrieve real-time listing prices and calculate the estimated landed cost.
Deliverables:
services/scraper.py: Queries proxy platforms (e.g., Buyee search URL for Mercari) using search_query_ja. Parses the top 3-5 prices and calculates median/lowest listing price in JPY.
services/pricing.py: Converts JPY to TWD with standard conversion formula:
Landed Cost (TWD)=(Price 
JPY
​	
 ×Rate 
JPY/TWD
​	
 )+Proxy Fee+Estimated Shipping
Generates markup alert flag: Overpriced if Price 
FB
​	
 >Landed Cost×1.3.
Phase 4: LINE Flex Message UI & Affiliate Integration
Goal: Build an engaging, high-conversion visual card for the user.
Deliverables:
services/flex_builder.py: Constructs a LINE Flex Message bubble displaying:
Item thumbnail & Japanese title.
Price comparison: FB Resale Price vs. Japan Landed Estimate.
Status indicator: 🟢 Fair Price / 🔴 Excessive Markup.
Direct CTA Button: URL injected with AFFILIATE_TAG pointing directly to the proxy search results.
Integration of all components into the main /api/webhook pipeline.
5. Antigravity Agent Rules (.agents/rules/project_rules.md)
Markdown
# Agent Execution Guidelines

1. **Architecture & Statelessness**:
   - The application runs on serverless functions. Never persist state to local disk.
   - Use asynchronous handlers (`async def`) for all network I/O operations.

2. **Error Handling & Graceful Degradation**:
   - Never allow unhandled exceptions to return HTTP 500 to LINE servers.
   - If scraping or parsing fails, return a polite fallback text message:
     "無法辨識此貼文內容，請確認是否包含清楚的商品名稱與作品名。" (Unable to parse post content. Please ensure product name and series are included.)

3. **Cost & Latency Optimization**:
   - Strictly use `gemini-1.5-flash` for entity parsing to maintain sub-second latency and minimal token consumption.
   - Set timeouts for all outgoing HTTP requests (maximum 5.0 seconds).

4. **Code Style & Type Safety**:
   - Enforce Python 3.11+ type hints across all functions and classes.
   - Use Pydantic models for request/response validation.
6. Environment Variables Reference
程式碼片段
# LINE Messaging API Credentials
LINE_CHANNEL_SECRET=your_line_channel_secret
LINE_CHANNEL_ACCESS_TOKEN=your_line_channel_access_token

# Google AI Studio API Key
GEMINI_API_KEY=your_gemini_api_key

# Affiliate Tracking Parameters
BUYEE_AFFILIATE_ID=your_affiliate_id_here
DEFAULT_EXCHANGE_RATE_JPY_TWD=0.21
DEFAULT_ESTIMATED_SHIPPING_TWD=150