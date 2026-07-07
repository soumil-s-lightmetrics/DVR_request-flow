"""
Pinecone Vector Store Sync Script

This script synchronizes Freshdesk articles to Pinecone vector store with LLM-powered
attribute extraction. It:
1. Fetches articles from Freshdesk API
2. Chunks article content
3. Uses GPT-5-mini (with medium reasoning effort) to extract technical attributes from each chunk
4. Generates embeddings using Ollama
5. Upserts vectors to Pinecone with rich metadata
6. Tracks sync status in PostgreSQL

The LLM uses a minimalist, dependency-based approach: only tagging when there's a
hard dependency that would make the content useless or misleading if absent.

Core Principle: "Tags define eligibility, not completeness"
"""

from dotenv import load_dotenv

load_dotenv()

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp
import asyncpg
from html_to_markdown import convert_to_markdown
from langchain.text_splitter import RecursiveCharacterTextSplitter
from openai import OpenAI
from pinecone import Pinecone
from pydantic import BaseModel
from tqdm import tqdm

from logger import configure_logger, get_log_formatter, get_log_handler
# Import attribute conversion utilities
from utils.attribute_parser import (get_attributes_from_tags,
                                    validate_attributes)
from utils.pg_connections import get_connection_url_pg

# ============================================================================
# Environment Validation
# ============================================================================

REQUIRED_ENV_VARS = [
    "PINECONE_API_KEY",
    "PINECONE_INDEX_HOST",
    "PINECONE_INDEX_NAME",
    "OPENAI_API_KEY",
    "FRESHDESK_API_BASE_URL",
    "FRESHDESK_API_KEY",
]

missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    raise EnvironmentError(
        f"Missing required environment variables: {', '.join(missing_vars)}"
    )

# Environment variables
FRESHDESK_API_BASE_URL = os.getenv("FRESHDESK_API_BASE_URL")
FRESHDESK_API_KEY = os.getenv("FRESHDESK_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_INDEX_HOST = os.getenv("PINECONE_INDEX_HOST")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

ALLOWED_CATEGORIES = (
    "General", "Hardware", "Events and Violations", "Companion Apps", "Master Portal",
    "Fleet Portal", "Rebranded Portal", "Device Application", "Backend and API", "SDK",
    "Intermediate Server", "Lisa Chat Bot",
)

# ============================================================================
# Global Connections
# ============================================================================

pool = None
pool_size = int(os.environ.get("LLM_POSTGRES_POOL_SIZE", "10"))

# Clients
openai_client = OpenAI(api_key=OPENAI_API_KEY)
pinecone_client = None
pinecone_index = None

# Logging
os.makedirs('logs', exist_ok=True)
sync_handler = get_log_handler('logs/pinecone_sync.log', logging.INFO, get_log_formatter())

# Set LOG_TO_STDOUT to enable console output via configure_logger
os.environ['LOG_TO_STDOUT'] = 'true'
sync_logger = configure_logger('pinecone_sync', [sync_handler])
sync_logger.propagate = False  # Prevent propagation to root logger

previous_millis = datetime.now().timestamp()

# ============================================================================
# Logging Utilities
# ============================================================================

def logtimedelta():
    """Calculate time delta since last log."""
    global previous_millis
    current_millis = datetime.now().timestamp()
    val = round(current_millis - previous_millis, 3)
    previous_millis = current_millis
    return val

def logprefix():
    """Generate log prefix with timestamp and delta."""
    return f"{datetime.now().isoformat()}({logtimedelta()}) "

# ============================================================================
# Progress Bar Utilities
# ============================================================================

def create_progress_bar(total: int, desc: str, position: int = 0) -> tqdm:
    """
    Create a tqdm progress bar for sync operations.

    Args:
        total: Total number of items to process
        desc: Description to display with progress bar
        position: Position for nested progress bars (0 = main)

    Returns:
        tqdm progress bar instance
    """
    return tqdm(
        total=total,
        desc=desc,
        position=position,
        leave=True,
        ncols=100,
        bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]'
    )

# ============================================================================
# HTTP Utilities with Retry Logic
# ============================================================================

async def fetch_with_retry(
    url: str,
    method: str = "GET",
    params: Optional[Dict] = None,
    data: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    auth: Optional[aiohttp.BasicAuth] = None,
    retries: int = 3
) -> Optional[Dict]:
    """
    Fetch URL with exponential backoff retry logic.

    Handles:
    - Rate limiting (429 status)
    - Transient errors with exponential backoff
    - Automatic retry with configurable attempts

    Args:
        url: URL to fetch
        method: HTTP method (GET, POST, etc.)
        params: Query parameters
        data: Request body
        headers: HTTP headers
        auth: Authentication
        retries: Maximum number of retry attempts

    Returns:
        JSON response as dict, or None on failure
    """
    async with aiohttp.ClientSession() as session:
        for attempt in range(retries):
            try:
                async with session.request(
                    method, url, params=params, data=data, headers=headers, auth=auth
                ) as response:
                    if response.status == 429:  # Rate limit
                        retry_after = int(response.headers.get("Retry-After", 10))
                        sync_logger.warning(
                            f"{logprefix()}Rate limit hit. Retrying after {retry_after}s..."
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    response.raise_for_status()
                    return await response.json()

            except aiohttp.ClientError as e:
                sync_logger.error(
                    f"{logprefix()}HTTP error (attempt {attempt + 1}/{retries}): {e}"
                )
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise

# ============================================================================
# Freshdesk API Functions (Reused from openai_vectorstore_sync.py)
# ============================================================================

async def get_categories():
    """Fetch all Freshdesk solution categories."""
    sync_logger.info(f"{logprefix()}Fetching categories...")
    url = f"{FRESHDESK_API_BASE_URL}/api/v2/solutions/categories"
    auth = aiohttp.BasicAuth(FRESHDESK_API_KEY, 'X')
    return await fetch_with_retry(url, auth=auth)

async def get_folders(category_id: int):
    """Fetch all folders within a category."""
    sync_logger.info(f"{logprefix()}Fetching folders for category ID {category_id}...")
    url = f"{FRESHDESK_API_BASE_URL}/api/v2/solutions/categories/{category_id}/folders"
    auth = aiohttp.BasicAuth(FRESHDESK_API_KEY, 'X')
    return await fetch_with_retry(url, auth=auth)

async def get_article_by_id(article_id: int):
    """
    Fetch a single article by ID from Freshdesk.

    Args:
        article_id: The Freshdesk article ID

    Returns:
        Article dictionary or None if not found
    """
    auth = aiohttp.BasicAuth(FRESHDESK_API_KEY, 'X')
    url = f"{FRESHDESK_API_BASE_URL}/api/v2/solutions/articles/{article_id}"

    try:
        sync_logger.info(f"{logprefix()}Fetching article ID {article_id}...")
        article = await fetch_with_retry(url, auth=auth)

        # Fetch category and folder names for metadata
        if article:
            # Get folder info to get category
            folder_url = f"{FRESHDESK_API_BASE_URL}/api/v2/solutions/folders/{article.get('folder_id')}"
            folder = await fetch_with_retry(folder_url, auth=auth)

            if folder:
                # Get category info
                category_url = f"{FRESHDESK_API_BASE_URL}/api/v2/solutions/categories/{folder.get('category_id')}"
                category = await fetch_with_retry(category_url, auth=auth)

                # Add metadata
                article['category'] = category.get('name', 'Unknown') if category else 'Unknown'
                article['folder'] = folder.get('name', 'Unknown')

        return article
    except Exception as e:
        sync_logger.error(f"{logprefix()}Error fetching article {article_id}: {e}")
        return None

async def get_articles(folder_id: int):
    """Fetch all articles within a folder (paginated)."""
    all_articles = []
    page = 1
    auth = aiohttp.BasicAuth(FRESHDESK_API_KEY, 'X')

    while True:
        sync_logger.info(
            f"{logprefix()}Fetching articles for folder ID {folder_id}, page {page}..."
        )
        url = f"{FRESHDESK_API_BASE_URL}/api/v2/solutions/folders/{folder_id}/articles"
        articles = await fetch_with_retry(url, params={"page": page}, auth=auth)

        if not articles:
            break

        all_articles.extend(articles)
        page += 1

    return all_articles

async def get_all_articles():
    """
    Fetch all articles from Freshdesk across all categories and folders.

    Returns:
        List of article dictionaries with metadata:
        {
            'id': int,
            'title': str,
            'description': str (HTML),
            'updated_at': str (ISO timestamp),
            'status': int (2 = published),
            'category': str,
            'folder': str,
            ...
        }
    """
    sync_logger.info(f"{logprefix()}Starting to fetch all articles from Freshdesk...")
    all_articles = []

    categories = await get_categories()

    for category in categories:
        category_name = category.get("name", "")

        # Filter allowed categories
        if category_name not in ALLOWED_CATEGORIES:
            continue

        sync_logger.info(f"{logprefix()}Processing category: {category_name}")

        folders = await get_folders(category["id"])

        for folder in folders:
            folder_name = folder.get("name", "")
            sync_logger.info(f"{logprefix()}Processing folder: {folder_name}")

            articles = await get_articles(folder["id"])

            # Enrich articles with category and folder info
            for article in articles:
                article["category"] = category_name
                article["folder"] = folder_name

            all_articles.extend(articles)

    sync_logger.info(
        f"{logprefix()}Finished fetching articles. Total: {len(all_articles)}"
    )
    return all_articles

# ============================================================================
# Chunking Logic
# ============================================================================

def chunk_article(article: Dict) -> List[Dict]:
    """
    Chunk article content using RecursiveCharacterTextSplitter.

    Args:
        article: Article dictionary with 'description' (HTML) and metadata

    Returns:
        List of chunk dictionaries:
        {
            'chunk_index': int,
            'chunk_text': str,
            'article_id': int,
            'article_title': str,
            'category': str,
            'folder': str,
        }
    """
    # Convert HTML to markdown for better readability
    html_content = article.get("description", "")

    if not html_content:
        sync_logger.warning(
            f"{logprefix()}Article {article['id']} has no content, skipping..."
        )
        return []

    # Convert to markdown
    markdown_content = convert_to_markdown(html_content)

    # Initialize text splitter (1000 chunk size, 200 overlap)
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
        separators=["\n\n", "\n", " ", ""]
    )

    # Split into chunks
    chunks = text_splitter.split_text(markdown_content)

    # Build chunk dictionaries with metadata
    chunk_dicts = []
    for idx, chunk_text in enumerate(chunks):
        chunk_dicts.append({
            'chunk_index': idx,
            'chunk_text': chunk_text,
            'article_id': article['id'],
            'article_title': article.get('title', ''),
            'category': article.get('category', ''),
            'folder': article.get('folder', ''),
        })

    sync_logger.debug(
        f"{logprefix()}Chunked article {article['id']} into {len(chunks)} chunks"
    )

    return chunk_dicts

# ============================================================================
# LLM Attribute Extraction
# ============================================================================

# Pydantic Model for LLM Response
class Attributes(BaseModel):
    """Structured attributes extracted from documentation chunks."""
    fleet_portal_version: str
    master_portal_version: str
    device_apk_version: str
    device_models: List[str]
    plans_nin: List[str]
    event_types: List[str]
    required_features: List[str]

class AttributeExtractionResponse(BaseModel):
    """Structured response format for attribute extraction.

    Ensures LLM returns attributes in a consistent object format with all required fields.
    """
    attributes: Attributes

# LLM Prompt for attribute extraction (minimalist decision tree approach)
ATTRIBUTE_EXTRACTION_SYSTEM_PROMPT = """You are a technical documentation analyzer for a fleet management system called LISA.

Your task: Extract ONLY the version attributes (fleet_portal_version, master_portal_version, device_apk_version) where there's a HARD DEPENDENCY. Use defaults otherwise.
For all other attributes, use defaults unless specified otherwise.

This task applies to INDIVIDUAL CHUNKS of an article, NOT the complete article
Each chunk is evaluated independently and should be tagged according to its own content.

CORE PRINCIPLE: Tags define eligibility, not completeness.
- For versions: Tag = "This article is USELESS without X version".
- For all other attributes, use the defaults unless a clear value is specified in the content.

CRITICAL: ALWAYS prioritize the Article Title for version context. If title says "Fleet Portal v10.7.0", that's the primary version reference for Fleet Portal.

SYSTEM ARCHITECTURE:
- Fleet Portal: Web application for fleet managers (version format: X.Y.Z)
- Master Portal: Administrative backend system (version format: X.Y.Z)
- Device APK: Mobile/camera application (version format: X.Y)

NEVER confuse these systems - they have independent version numbers!

IMPORTANT: Each chunk is evaluated independently. A single chunk CAN and SHOULD specify:
- DIFFERENT versions for Fleet Portal, Master Portal, and Device APK if applicable
- For example: "This feature requires Fleet Portal v10.7.0 AND Master Portal v5.26.0 AND Device APK v1.20"
- Extract ALL applicable version requirements - do NOT omit any if the content mentions them
- If a chunk mentions multiple systems, tag them ALL with their respective versions

DECISION TREE FOR EACH ATTRIBUTE:

1. fleet_portal_version (format: "X.Y.Z")
   ASK: "What is the EARLIEST Fleet Portal version where this content becomes valid?"
   EXTRACT IF:
     - Article Title contains "Fleet Portal Release Notes vX.Y.Z" → USE X.Y.Z (unless chunk overrides it)
     - Content has Fleet Portal version requirement (e.g., "requires Fleet Portal v10.9+", "introduced in Fleet Portal 9.18")
   USE DEFAULT "0.0.0" IF: General content, no Fleet Portal version dependency
   IMPORTANT:
     - Extract MINIMUM version only
     - Can be specified alongside master_portal_version and device_apk_version in same chunk
   NEVER CONFUSE WITH: Master Portal versions, Device APK versions
   NEVER TAG: Enhancements, UI changes, "recommended version"

2. master_portal_version (format: "X.Y.Z")
   ASK: "What is the EARLIEST Master Portal version required?"
   EXTRACT IF:
     - Article Title contains "Master Portal Release Notes vX.Y.Z" → USE X.Y.Z (unless chunk overrides it)
     - Content has Master Portal version requirement (e.g., "requires Master Portal v5.26+")
   USE DEFAULT "0.0.0" IF: General content, no Master Portal version dependency
   IMPORTANT:
     - Extract MINIMUM version only
     - Can be specified alongside fleet_portal_version and device_apk_version in same chunk
   NEVER CONFUSE WITH: Fleet Portal versions, Device APK versions

3. device_apk_version (format: "X.Y")
   ASK: "What is the EARLIEST device APK required?"
   EXTRACT IF:
     - Article Title contains "Release Notes vX.Y" → USE X.Y (unless chunk overrides it)
     - Explicit Device APK requirement (e.g., "requires device app v1.20+", "camera firmware v1.20+")
   USE DEFAULT "0.0" IF: No Device APK dependency mentioned
   IMPORTANT:
     - Device APK uses X.Y format (2 numbers), NOT X.Y.Z
     - Can be specified alongside fleet_portal_version and master_portal_version in same chunk
   NEVER CONFUSE WITH: Fleet Portal versions, Master Portal versions
   NEVER TAG: Optional upgrades

4. device_models (array of strings: ["model1", "model2"])
   ASK: "Does this behavior exist ONLY on certain camera models?"
   EXTRACT IF: Model-specific content
   USE EMPTY [] IF: General content, applies to all models
   Known models: mitac-gemini, mitac-sprint-k220, mitac-evo-k265, jimi-jc261, jimi-jc261p, jimi-jc450, jimi-jc400, jimi-jc400p
   NEVER TAG: Lists of supported models without restriction

5. plans_nin (array of strings: ["PLAN1"])
   ASK: "Is this content describing a feature that is NOT available on the SHIELD plan?"
   USE ["SHIELD"] IF: The feature is one of:
        - Scheduled Reports
        - Tagging
        - Access Control
        - Custom Events
        - Coaching
        - Custom User Roles
   USE EMPTY [] otherwise
   Known plans: SHIELD
   NEVER TAG: Marketing mentions, pricing, general feature lists

6. event_types (array of strings: ["Event1", "Event2"])
   EXTRACT IF: Content mentions any event name or alternative wording from the known list—mentioning alone is sufficient, NOT just hard dependency.
   USE EMPTY [] only if no events are mentioned.
   Known events: Traffic-Speed-Violated, Cornering, Traffic-STOP-Sign-Violated, Harsh-Braking, Harsh-Acceleration, Tail-Gating-Detected, Lane-Drift-Found, Distracted-Driving, MaxSpeedExceeded, Drowsy-Driving-Detected, Forward-Collision-Warning, Cellphone-Distracted-Driving, Smoking-Distracted-Driving, Drinking-Distracted-Driving, Unbuckled-Seat-Belt, Lizard-Eye-Distracted-Driving, Roll-Over-Detected, Texting-Distracted-Driving, Traffic-Light-Violated, Driver-Fatigue-Detected
   Alternative wordings:
     - "harsh braking" / "hard braking" → Harsh-Braking
     - "harsh acceleration" / "hard acceleration" → Harsh-Acceleration
     - "speeding" → Traffic-Speed-Violated or MaxSpeedExceeded (use context)
     - "tailgating" / "TG" → Tail-Gating-Detected
     - "lane drift" → Lane-Drift-Found
     - "drowsy driving" / "drowsy" / "drowsiness" → Drowsy-Driving-Detected
     - "driver fatigue" → Driver-Fatigue-Detected
     - "seatbelt" / "seat belt" → Unbuckled-Seat-Belt
     - "fcw" / "collision warning" → Forward-Collision-Warning
     - "texting while driving" / "texting" → Texting-Distracted-Driving
     - "cellphone use" / "cell phone use" → Cellphone-Distracted-Driving
     - "smoking while driving" / "smoking" → Smoking-Distracted-Driving
     - "rollover" / "roll-over" → Roll-Over-Detected
     - "red light violation" / "traffic light" → Traffic-Light-Violated

   CRITICAL: ONLY use events from the Known events list above. NEVER invent new event names

7. required_features (array of strings: ["Feature1", "Feature2"])
   ASK: "Is this content SPECIFIC to one or more particular features?"
   EXTRACT IF:
     - Content explains/configures/troubleshoots a specific feature (e.g., "How to use ADAS")
     - Content demonstrates functionality that requires a specific feature
     - Content is meaningless without that feature being enabled
     - Content is an overview or introduction to a specific feature
   USE EMPTY [] IF:
     - General system content not tied to any specific feature
     - Content applies across multiple features equally
     - Content about basic functionality available regardless of features
   Example features: ADAS, DMS, Live-Streaming, Cloud-Storage, Coaching, Tagging, Custom-Events, Geofencing, GPS-Tracking etc.,
   IMPORTANT: Tag the specific feature even if it's just explaining what that feature does. Feel free to invent feature names as needed based on context.
   NEVER TAG: Brief mentions or recommendations to use a feature in passing

RESPONSE FORMAT (JSON object with 'attributes' object, no explanation):
Return a JSON object with an 'attributes' key containing an object with exactly 7 fields. Empty string attributes use empty string (""), empty array attributes use empty array ([]).

Example Response:
{
  "attributes": {
    "fleet_portal_version": "10.9.0",
    "master_portal_version": "5.26.0",
    "device_apk_version": "1.20",
    "device_models": ["jimi-jc261"],
    "plans_nin": ["SHIELD"],
    "event_types": ["Traffic-Light-Violated"],
    "required_features": ["ADAS"]
  }
}

VALIDATION CHECKLIST:
- Used defaults (0.0.0, 0.0.0, 0.0, []) for general content?
- Extracted MINIMUM version, not latest?
- Tagged event_types when mentioned, not just for hard dependency?
- Avoided tagging overviews, mentions, recommendations?
- Did NOT confuse Fleet Portal, Master Portal, and Device APK versions?
- Prioritized Article Title for version context?"""



async def extract_attributes_with_llm(chunks: List[Dict]) -> List[Dict]:
    """
    Extract technical attributes from chunks using GPT-5-mini with medium reasoning effort.

    Uses minimalist decision tree approach: only tags hard dependencies.

    Args:
        chunks: List of chunk dictionaries with 'chunk_text', 'article_title', etc.

    Returns:
        List of extracted attribute dictionaries (one per chunk).
        Each dict contains all 7 required attribute fields.

    Example:
        >>> chunks = [{'chunk_text': 'Requires v10.9...', 'article_title': 'Setup'}]
        >>> tags = await extract_attributes_with_llm(chunks)
        >>> tags[0]
        {'fleet_portal_version': '10.9.0', 'device_apk_version': '0.0', ...}
    """
    extracted_tags = []

    # Process in batches of 20 for efficiency
    batch_size = 20
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i+batch_size]

        # Create callable functions for each API call (to parallelize them)
        def make_api_call(chunk):
            """Create a function that makes the OpenAI API call for a chunk."""
            user_prompt = f"""
Article Title: {chunk['article_title']}
Freshdesk Category: {chunk['category']}
Freshdesk Folder: {chunk['folder']}

Chunk Content:
---
{chunk['chunk_text']}
---

IMPORTANT: Analyze ALL the information provided above (Article Title, Freshdesk Category, Freshdesk Folder AND Chunk Content) together to extract technical attributes.

Consider how the Category, Folder and Title provide context for interpreting the chunk content. Make your attribute extraction decisions based on the COMPLETE picture from all three sources of information, not just the chunk content alone.

Return the extracted attributes as a JSON array.
"""
            return openai_client.chat.completions.create(
                model="gpt-5-mini",
                messages=[
                    {"role": "system", "content": ATTRIBUTE_EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                reasoning_effort="medium",
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "attribute_extraction_response",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "attributes": {
                                    "type": "object",
                                    "properties": {
                                        "fleet_portal_version": {"type": "string"},
                                        "master_portal_version": {"type": "string"},
                                        "device_apk_version": {"type": "string"},
                                        "device_models": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        },
                                        "plans_nin": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        },
                                        "event_types": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        },
                                        "required_features": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        }
                                    },
                                    "required": [
                                        "fleet_portal_version",
                                        "master_portal_version",
                                        "device_apk_version",
                                        "device_models",
                                        "plans_nin",
                                        "event_types",
                                        "required_features"
                                    ],
                                    "additionalProperties": False
                                }
                            },
                            "required": ["attributes"],
                            "additionalProperties": False
                        }
                    }
                }
            )

        # Execute all API calls in parallel using thread pool
        responses = await asyncio.gather(*[
            asyncio.to_thread(make_api_call, chunk) for chunk in batch
        ])

        # Parse responses (simplified with structured outputs)
        for response in responses:
            try:
                content = response.choices[0].message.content
                parsed = json.loads(content)

                # Structured output guarantees {"attributes": {...}} format
                if isinstance(parsed, dict) and 'attributes' in parsed and isinstance(parsed['attributes'], dict):
                    extracted_tags.append(parsed['attributes'])
                else:
                    # Fallback: log unexpected format
                    sync_logger.warning(
                        f"Unexpected LLM response format (expected 'attributes' object): {parsed}"
                    )
                    extracted_tags.append({
                        "fleet_portal_version": "0.0.0",
                        "master_portal_version": "0.0.0",
                        "device_apk_version": "0.0",
                        "device_models": [],
                        "plans_nin": [],
                        "event_types": [],
                        "required_features": []
                    })

            except (json.JSONDecodeError, KeyError, IndexError) as e:
                sync_logger.error(f"Error parsing LLM response: {e}")
                # Use defaults on parse error
                extracted_tags.append({
                    "fleet_portal_version": "0.0.0",
                    "master_portal_version": "0.0.0",
                    "device_apk_version": "0.0",
                    "device_models": [],
                    "plans_nin": [],
                    "event_types": [],
                    "required_features": []
                })

        sync_logger.info(f"{logprefix()}Extracted attributes for batch {i//batch_size + 1}")

    return extracted_tags

# ============================================================================
# Embedding Generation
# ============================================================================

def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Generate embeddings using OpenAI text-embedding-3-small.

    Args:
        texts: List of text strings to embed

    Returns:
        List of embedding vectors (1536-dimensional)
    """
    embeddings = []

    # Process in batches for efficiency (OpenAI allows up to 2048 inputs per call)
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        try:
            # Call OpenAI embeddings API directly
            response = openai_client.embeddings.create(
                model="text-embedding-3-small",
                input=batch,
                encoding_format="float"
            )

            # Extract embeddings from response
            batch_embeddings = [item.embedding for item in response.data]
            embeddings.extend(batch_embeddings)

            sync_logger.debug(
                f"{logprefix()}Generated embeddings for batch {i//batch_size + 1} "
                f"({len(batch)} texts)"
            )
        except Exception as e:
            sync_logger.error(f"Error generating embeddings for batch: {e}")
            # Use zero vectors as fallback for failed batch
            embeddings.extend([[0.0] * 1536] * len(batch))

    return embeddings

# ============================================================================
# Pinecone Operations
# ============================================================================

async def initialize_pinecone():
    """Initialize Pinecone client and index connection."""
    global pinecone_client, pinecone_index

    sync_logger.info(f"{logprefix()}Initializing Pinecone client...")

    # Initialize Pinecone
    pinecone_client = Pinecone(
        api_key=PINECONE_API_KEY,
    )

    # Get index with secure=True for remote cloud connection
    pinecone_index = pinecone_client.Index(host=PINECONE_INDEX_HOST)

    sync_logger.info(f"{logprefix()}Pinecone initialized successfully (remote cloud host)")

async def delete_article_vectors(article_id: int) -> int:
    """
    Delete all vectors for an article from Pinecone.

    Pinecone doesn't support metadata-based bulk delete, so we:
    1. Query for all vectors with this article_id
    2. Delete by vector IDs

    Args:
        article_id: Freshdesk article ID

    Returns:
        Number of vectors deleted
    """
    try:
        # Query for all vectors with this article_id
        # Use dummy vector for query
        results = pinecone_index.query(
            vector=[0.0] * 1536,
            filter={"fd_article_id": article_id},
            top_k=10000,
            include_metadata=False,
            namespace="__default__"
        )

        vector_ids = [match.id for match in results.matches]

        if not vector_ids:
            return 0

        # Delete in batches of 1000
        for i in range(0, len(vector_ids), 1000):
            batch = vector_ids[i:i+1000]
            pinecone_index.delete(ids=batch, namespace="__default__")

        sync_logger.info(
            f"{logprefix()}Deleted {len(vector_ids)} vectors for article {article_id}"
        )

        return len(vector_ids)

    except Exception as e:
        sync_logger.error(f"Error deleting vectors for article {article_id}: {e}")
        return 0

async def upsert_article_vectors(
    article_id: int,
    chunks: List[Dict],
    tags_list: List[List[str]],
    embeddings: List[List[float]]
) -> int:
    """
    Upsert vectors for an article to Pinecone.

    Args:
        article_id: Freshdesk article ID
        chunks: List of chunk dictionaries
        tags_list: List of extracted tags (one per chunk)
        embeddings: List of embedding vectors (one per chunk)

    Returns:
        Number of vectors upserted
    """
    vectors = []

    for i, (chunk, tags, embedding) in enumerate(zip(chunks, tags_list, embeddings)):
        # Convert LLM tags to Pinecone metadata
        attributes = get_attributes_from_tags(tags)

        # Validate attributes
        valid, error = validate_attributes(attributes)
        if not valid:
            sync_logger.warning(
                f"Invalid attributes for article {article_id} chunk {i}: {error}"
            )
            continue

        # Build complete metadata
        metadata = {
            # Article identifiers
            "fd_article_id": article_id,
            "fd_article_url": f"https://lightmetrics.freshdesk.com/a/solutions/articles/{article_id}",
            "fd_article_title": chunk['article_title'],
            "fd_category": chunk['category'],
            "fd_folder": chunk['folder'],

            # Chunk information
            "chunk_index": chunk['chunk_index'],
            "chunk_text": chunk['chunk_text'],

            # Extracted attributes
            **attributes
        }

        # Generate stable vector ID (no timestamp - allows Pinecone to replace on upsert)
        vector_id = f"art{article_id}_ch{i}"

        vectors.append({
            "id": vector_id,
            "values": embedding,
            "metadata": metadata
        })

    # Upsert in batches of 100
    batch_size = 100
    total_upserted = 0

    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i+batch_size]
        try:
            pinecone_index.upsert(vectors=batch, namespace="__default__")
            total_upserted += len(batch)
            sync_logger.debug(
                f"{logprefix()}Upserted batch {i//batch_size + 1} "
                f"({len(batch)} vectors) for article {article_id}"
            )
        except Exception as e:
            sync_logger.error(f"Error upserting batch for article {article_id}: {e}")

    sync_logger.info(
        f"{logprefix()}Upserted {total_upserted} vectors for article {article_id}"
    )

    return total_upserted

# ============================================================================
# Database Operations
# ============================================================================

async def should_sync_article(article: Dict, pool) -> bool:
    """
    Determine if article needs syncing based on timestamps and content hash.

    Args:
        article: Article dictionary from Freshdesk
        pool: asyncpg connection pool

    Returns:
        True if article should be synced
    """
    article_id = article['id']

    async with pool.acquire() as conn:
        # Check if article exists in sync status table
        db_entry = await conn.fetchrow(
            "SELECT last_synced_at, content_hash FROM pinecone_article_sync_status WHERE fd_article_id = $1",
            article_id
        )

        # New article - always sync
        if db_entry is None:
            return True

        # Compare timestamps
        article_updated = datetime.fromisoformat(article['updated_at'].replace('Z', '+00:00'))
        last_synced = db_entry['last_synced_at'].replace(tzinfo=timezone.utc)

        if article_updated > last_synced:
            return True

        # Content hash check
        current_hash = hashlib.sha256(article['description'].encode()).hexdigest()
        if db_entry['content_hash'] != current_hash:
            return True

    return False

async def update_sync_status(
    article_id: int,
    fd_article_title: str,
    vector_count: int,
    extracted_tags: List[List[str]],
    content_hash: str,
    status: str = 'completed',
    error_message: Optional[str] = None,
    pool = None
):
    """
    Update sync status in database.

    Args:
        article_id: Freshdesk article ID
        fd_article_title: Freshdesk article title for readability
        vector_count: Number of vectors upserted
        extracted_tags: All tags extracted from chunks
        content_hash: SHA256 hash of article content
        status: 'completed' or 'failed'
        error_message: Error message if failed
        pool: asyncpg connection pool
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO pinecone_article_sync_status
            (fd_article_id, fd_article_title, vector_count, extracted_tags, last_synced_at, sync_status, error_message, content_hash, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (fd_article_id)
            DO UPDATE SET
                fd_article_title = EXCLUDED.fd_article_title,
                vector_count = EXCLUDED.vector_count,
                extracted_tags = EXCLUDED.extracted_tags,
                last_synced_at = EXCLUDED.last_synced_at,
                sync_status = EXCLUDED.sync_status,
                error_message = EXCLUDED.error_message,
                content_hash = EXCLUDED.content_hash,
                updated_at = EXCLUDED.updated_at
        """, article_id, fd_article_title, vector_count, json.dumps(extracted_tags), datetime.now(timezone.utc),
            status, error_message, content_hash, datetime.now(timezone.utc))

# ============================================================================
# Main Sync Orchestration
# ============================================================================

async def sync_article_to_pinecone(article: Dict, pool) -> Dict:
    """
    Sync a single article to Pinecone with LLM attribute extraction.

    Pipeline:
    1. Chunk article content
    2. Extract attributes with LLM
    3. Generate embeddings
    4. Smart deletion (only if chunk count decreased)
    5. Upsert new vectors (replaces existing with stable IDs)
    6. Update sync status

    Note: Uses stable vector IDs (art{id}_ch{idx}) so Pinecone automatically
    replaces vectors on upsert. Deletion only needed when chunk count decreases.

    Args:
        article: Article dictionary from Freshdesk
        pool: asyncpg connection pool

    Returns:
        Dictionary with sync results:
        {
            'article_id': int,
            'vector_count': int,
            'status': 'completed' or 'failed',
            'error': str (if failed)
        }
    """
    article_id = article['id']
    article_title = article.get('title', 'Untitled')

    try:
        sync_logger.info(f"{logprefix()}Syncing article {article_id}: {article_title}")

        # Step 1: Chunk article
        chunks = chunk_article(article)
        if not chunks:
            sync_logger.warning(f"No chunks for article {article_id}, skipping")
            return {'article_id': article_id, 'vector_count': 0, 'status': 'skipped'}

        # Step 2: Extract attributes with LLM
        sync_logger.info(f"{logprefix()}Extracting attributes for {len(chunks)} chunks...")
        tags_list = await extract_attributes_with_llm(chunks)

        # Step 3: Generate embeddings
        sync_logger.info(f"{logprefix()}Generating embeddings...")
        chunk_texts = [c['chunk_text'] for c in chunks]
        embeddings = generate_embeddings(chunk_texts)

        # Step 4: Check if we need to delete old vectors
        # With stable vector IDs, upsert automatically replaces existing vectors
        # We only need to delete if chunk count decreased (to remove orphaned chunks)
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT vector_count FROM pinecone_article_sync_status WHERE fd_article_id = $1",
                article_id
            )

        new_chunk_count = len(chunks)
        old_chunk_count = existing['vector_count'] if existing else 0

        if old_chunk_count > new_chunk_count:
            # Article got shorter - delete orphaned chunks
            sync_logger.info(
                f"{logprefix()}Chunk count decreased ({old_chunk_count} → {new_chunk_count}), "
                f"deleting old vectors..."
            )
            await delete_article_vectors(article_id)
        else:
            # Chunk count same or increased - upsert will handle it
            sync_logger.info(
                f"{logprefix()}Chunk count: {new_chunk_count} (was {old_chunk_count}), "
                f"upsert will replace existing vectors"
            )

        # Step 5: Upsert new vectors (replaces existing vectors with same IDs)
        vector_count = await upsert_article_vectors(article_id, chunks, tags_list, embeddings)

        # Step 6: Update sync status
        content_hash = hashlib.sha256(article['description'].encode()).hexdigest()
        await update_sync_status(
            article_id, article_title, vector_count, tags_list, content_hash, 'completed', None, pool
        )

        sync_logger.info(
            f"{logprefix()}Successfully synced article {article_id} ({vector_count} vectors)"
        )

        return {
            'article_id': article_id,
            'vector_count': vector_count,
            'status': 'completed'
        }

    except Exception as e:
        sync_logger.error(f"{logprefix()}Error syncing article {article_id}: {e}", exc_info=True)

        # Update status as failed
        content_hash = hashlib.sha256(article.get('description', '').encode()).hexdigest()
        await update_sync_status(
            article_id, article_title, 0, [], content_hash, 'failed', str(e), pool
        )

        return {
            'article_id': article_id,
            'vector_count': 0,
            'status': 'failed',
            'error': str(e)
        }

async def main(article_ids: Optional[List[int]] = None, force: bool = False):
    """
    Main sync orchestration.

    Args:
        article_ids: Optional list of specific article IDs to sync.
                     If None, syncs all changed articles.
        force: If True, bypass incremental sync and force re-sync all articles.
    """
    global pool

    start_time = datetime.now(timezone.utc)
    sync_logger.info(f"{logprefix()}=== Starting Pinecone Sync ===")

    # Initialize connections
    sync_logger.info(f"{logprefix()}Initializing connections...")

    # PostgreSQL
    pool = await asyncpg.create_pool(get_connection_url_pg(), min_size=5, max_size=pool_size)

    # Pinecone
    await initialize_pinecone()

    # Fetch articles from Freshdesk
    if article_ids:
        # Efficient: Fetch only specified articles by ID
        sync_logger.info(f"{logprefix()}Fetching {len(article_ids)} specified articles by ID...")
        all_articles = []
        for article_id in article_ids:
            article = await get_article_by_id(article_id)
            if article:
                all_articles.append(article)
            else:
                sync_logger.warning(f"{logprefix()}Article ID {article_id} not found")
    else:
        # Full sync: Fetch all articles
        sync_logger.info(f"{logprefix()}Fetching all articles from Freshdesk...")
        all_articles = await get_all_articles()

    # Filter out unpublished articles (status != 2) unless in "Lisa Chat Bot" category
    filtered_articles = []
    skipped_unpublished = 0
    for article in all_articles:
        article_status = article.get('status', 0)
        article_category = article.get('category', '')

        # Skip if not published (status != 2) and not in "Lisa Chat Bot" category
        if article_status != 2 and article_category != "Lisa Chat Bot":
            sync_logger.debug(
                f"{logprefix()}Skipping unpublished article {article['id']} "
                f"(status={article_status}, category={article_category})"
            )
            skipped_unpublished += 1
            continue

        filtered_articles.append(article)

    if skipped_unpublished > 0:
        sync_logger.info(
            f"{logprefix()}Skipped {skipped_unpublished} unpublished articles "
            f"({len(filtered_articles)} articles remaining)"
        )

    all_articles = filtered_articles

    # Determine which articles need syncing
    articles_to_sync = []
    for article in all_articles:
        if force or await should_sync_article(article, pool):
            articles_to_sync.append(article)

    if force:
        sync_logger.info(f"{logprefix()}Force mode enabled - syncing all articles")

    sync_logger.info(
        f"{logprefix()}Found {len(articles_to_sync)} articles to sync out of {len(all_articles)}"
    )

    # Sync articles with controlled concurrency (10 at a time)
    semaphore = asyncio.Semaphore(10)
    results = []

    # Create progress bar for article sync
    progress_bar = create_progress_bar(
        total=len(articles_to_sync),
        desc="Syncing articles",
        position=0
    )

    async def sync_with_limit(article):
        """Sync article and update progress bar."""
        async with semaphore:
            result = await sync_article_to_pinecone(article, pool)
            progress_bar.update(1)  # Update progress after each article
            return result

    tasks = [sync_with_limit(a) for a in articles_to_sync]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Close progress bar
    progress_bar.close()

    # Calculate statistics
    completed = sum(1 for r in results if isinstance(r, dict) and r['status'] == 'completed')
    failed = sum(1 for r in results if isinstance(r, dict) and r['status'] == 'failed')
    total_vectors = sum(r['vector_count'] for r in results if isinstance(r, dict))
    total_chunks = total_vectors  # Each chunk = one vector

    # Calculate LLM API calls (approximate: one call per chunk)
    llm_api_calls = total_chunks

    # Estimate cost (GPT-5-mini with medium reasoning: $0.25/1M input + $2.00/1M output)
    # Assume ~2250 tokens input, ~1250 tokens output per chunk
    llm_cost_estimate = (llm_api_calls * 2250 * 0.25 / 1_000_000) + (llm_api_calls * 1250 * 2.00 / 1_000_000)

    # Add embedding cost (text-embedding-3-small: $0.02/1M tokens)
    # Assume ~250 tokens per chunk (average for 1000 char chunks)
    embedding_cost_estimate = total_chunks * 250 * 0.02 / 1_000_000

    # Total cost
    total_cost_estimate = llm_cost_estimate + embedding_cost_estimate

    # Record sync run in database
    end_time = datetime.now(timezone.utc)
    status = 'completed' if failed == 0 else 'partial' if completed > 0 else 'failed'

    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO pinecone_sync_runs
                (run_started_at, run_completed_at, total_articles_checked, articles_synced,
                 articles_failed, total_chunks_processed, total_vectors_upserted,
                 llm_api_calls, llm_cost_estimate, status, error_message)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """, start_time, end_time, len(all_articles), completed, failed,
                total_chunks, total_vectors, llm_api_calls, total_cost_estimate, status, None)
        sync_logger.info(f"{logprefix()}Sync run recorded in database")
    except Exception as e:
        sync_logger.error(f"{logprefix()}Failed to record sync run: {e}")

    # Close connections
    await pool.close()

    # Log summary
    duration = (datetime.now(timezone.utc) - start_time).total_seconds()
    sync_logger.info(f"{logprefix()}=== Sync Complete ===")
    sync_logger.info(f"Duration: {duration:.2f}s")
    sync_logger.info(f"Articles checked: {len(all_articles)}")
    sync_logger.info(f"Articles synced: {len(articles_to_sync)}")
    sync_logger.info(f"Completed: {completed}")
    sync_logger.info(f"Failed: {failed}")
    sync_logger.info(f"Total vectors upserted: {total_vectors}")
    sync_logger.info(f"LLM API calls: {llm_api_calls}")
    sync_logger.info(f"Estimated LLM cost: ${llm_cost_estimate:.4f}")
    sync_logger.info(f"Estimated embedding cost: ${embedding_cost_estimate:.4f}")
    sync_logger.info(f"Total estimated cost: ${total_cost_estimate:.4f}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Freshdesk articles to Pinecone")
    parser.add_argument(
        "--article-ids",
        type=str,
        help="Comma-separated list of article IDs to sync (optional)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-sync all articles, bypassing incremental sync checks"
    )

    args = parser.parse_args()

    article_ids = None
    if args.article_ids:
        article_ids = [int(id.strip()) for id in args.article_ids.split(',')]

    asyncio.run(main(article_ids, force=args.force))

