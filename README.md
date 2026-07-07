## Overview

LM-Knowledge Base (LISA) is a Flask-based RAG (Retrieval-Augmented Generation) API that powers an AI-driven knowledge assistant. It answers questions about documentation, support articles, and product knowledge using multiple vector store backends (OpenAI Vector Store, Pinecone, PGVector) and integrates with Freshdesk for support ticket automation.

## Key Commands

### Development Setup

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements-app.txt

# Run the Flask application (development)
python main.py
```

### Docker Operations

```bash
# Build Docker image
docker build -t chat-knowledge-base:latest .

# Run container (requires AWS Parameter Store access)
./start_container.sh
```

### Vector Store Management

```bash
# Sync Freshdesk articles to OpenAI Vector Store
python openai_vectorstore_sync.py

# Load bulk documents from JSON
python load_bulk_docs.py

# Load categorized documents
python load_bulk_docs_categorised.py

# Clean up OpenAI vector store
python openai_vectorstore_cleanup.py

# Resync vector store
python openai_vectorstore_resync.py
```

## Architecture

### Request Flow

The application uses a dual-architecture approach:

1. **Legacy Flow** (`/v1/llm-kb/get-answer`):
   - Uses `AssistantUtil` with OpenAI Assistants API
   - Thread-based conversation management
   - Citations from OpenAI Vector Store files

2. **New Flow** (`/v1/llm-kb/get-answer-new`):
   - Uses `PineconeOpenAIResponsesHandler` or `OpenAIResponsesHandler`
   - Fleet-aware with metadata-based filtering
   - Enhanced streaming with event-based responses

### Vector Store Strategy

Three vector store implementations coexist:

- **OpenAI Vector Store**: Primary production system, uses OpenAI Files API with file search
- **Pinecone**: Fleet-specific RAG with advanced metadata filtering (versions, device models, plans)
- **PGVector**: PostgreSQL-based fallback for LangChain integrations

Selection logic in `main.py`:
- Legacy endpoints → `AssistantUtil` → OpenAI Vector Store
- New endpoints → `PineconeOpenAIResponsesHandler` → Pinecone (or `OpenAIResponsesHandler` → OpenAI)
- LangChain chains → `PGVector`

### Session Management

Sessions are managed at two levels:
1. **Flask sessions**: Filesystem-backed, 31-day lifetime, session IDs prefixed with `CKB-`
2. **In-memory session maps**:
   - `sessionObjectMap`: Maps session_id → conversation functions (legacy)
   - `sessionMap`: Maps session_id → RAG handlers + previous response IDs (new)

OpenAI thread IDs are cached per session in `AssistantUtil._thread_cache` to maintain conversation continuity.

### Configuration Management

**S3 Config Manager** (`utils/s3_config_manager.py`):
- Pulls JSON configuration from S3 (`S3_BUCKET_NAME`, `S3_CONFIG_KEY`)
- Auto-refreshes every 3600 seconds (1 hour)
- Used for release notes, platform configurations, dynamic settings

**Fleet Config Manager** (`utils/fleet_config_manager.py`):
- Hardcoded fleet configurations (shield, non-shield, default)
- Provides version constraints, device models, plan types
- Generates metadata filters for Pinecone vector search

### Database Schema

PostgreSQL with pgvector extension. Key tables:

- `chat_history`: Stores Q&A pairs per session
- `feedback`: User feedback on responses (POSITIVE/NEGATIVE)
- `llm_source_docs`: Source documents with titles and HTML content
- `category_questions`: Pre-defined questions per category
- `langchain_pg_embedding`: PGVector embeddings for LangChain retriever

Connection pooling via `utils/pg_connections.py` (ThreadedConnectionPool, 5-50 connections).

### Tool & Function Calling

**AITools** (`tools/ai_tools.py`) executes dynamic functions:
- `fetch_latest_release_notes(platform_type)`: Fetches release notes from S3 config + Freshdesk
- Tool definitions in `tools/function_definitions.py` using OpenAI function schema

Tool execution flow:
```
LLM generates function_call → handle_tool_calls() → call_tool_function() → Freshdesk API/S3 → return results
```

## Module Organization

```
├── main.py                       # Flask API server, routes, session management
├── assistant_rag.py              # OpenAI Assistants API wrapper with streaming
├── rag_utils/
│   ├── openai_responses_rag.py   # OpenAI Responses API handler
│   ├── pinecone_openai_rag.py    # Pinecone + OpenAI integration
│   ├── llama_rag.py              # LlamaIndex integration
│   ├── rag_setup.py              # LangChain RAG chains
│   └── llama_parser.py           # Document parsing utilities
├── tools/
│   ├── ai_tools.py               # Tool execution engine
│   └── function_definitions.py  # OpenAI function schemas
├── utils/
│   ├── pg_connections.py         # Database connection pool
│   ├── vectorstore.py            # LangChain PGVector wrapper
│   ├── s3_config_manager.py      # S3 config with periodic refresh
│   ├── fleet_config_manager.py   # Fleet-specific configurations
│   ├── embeddings.py             # OpenAI embeddings wrapper
│   ├── freshdesk_api_util.py     # Freshdesk async API client
│   ├── collection_config.py      # Vector store collection names
│   ├── prompts.py                # System and user prompts
│   └── html_util.py              # HTML processing utilities
└── logger.py                     # Structured logging (access, debug)
```

## Environment Variables

Critical environment variables (loaded from AWS Parameter Store in production):

```bash
# OpenAI
OPENAI_API_KEY
OPENAI_ASSISTANT_ID
OPENAI_MODEL
OPENAI_VEC_STORE_ID

# Pinecone
PINECONE_API_KEY
PINECONE_INDEX_HOST
PINECONE_REMOTE_API_KEY

# PostgreSQL
LLM_POSTGRES_DATABASENAME
LLM_POSTGRES_USERNAME
LLM_POSTGRES_PASSWORD
LLM_POSTGRES_HOST
LLM_POSTGRES_PORT
PGVECTOR_COLLECTION_NAME

# Freshdesk
FRESHDESK_API_KEY
FRESHDESK_API_BASE_URL

# S3
S3_BUCKET_NAME
S3_CONFIG_KEY

# Application
PORT                              # Default: 5000
PREVIEW_KEY                       # API authentication header
LOG_TO_STDOUT                     # Default: false
```

## API Endpoints

### Health & Utility
- `GET /health-check`
- `GET /v1/llm-kb/health-check`
- `GET /v2/llm-kb/test-streaming`

### Question Answering
- `POST /v1/llm-kb/get-answer` - Legacy Q&A with OpenAI Assistants
  - Query params: `stream` (bool), `includeUserIntent` (bool)
- `POST /v1/llm-kb/get-answer-new` - Fleet-aware Q&A with Pinecone
  - Query params: `stream`, `fleetId`, `includeUserIntent`

### Document Reference
- `POST /v1/llm-kb/reference-doc` - Fetch document metadata by ID

### Feedback System
- `POST /v1/llm-kb/save-feedback` - Submit user feedback
- `POST /v1/llm-kb/update-feedback` - Update existing feedback
- `GET /v1/llm-kb/feedback-list` - Retrieve feedback by date range

### Categories
- `GET /v1/llm-kb/all-categories` - List all knowledge categories
- `GET /v1/llm-kb/category-questions` - Get questions for category

### Freshdesk Integration
- `POST /v1/llm-kb/generate-fd-ticket-response/<ticket_id>` - Auto-generate support responses

Authentication: All endpoints require `x-lm-preview-key` header matching `PREVIEW_KEY` env var.

## Streaming Responses

Streaming uses Server-Sent Events (SSE) with `text/event-stream`:

```
data: {"text": "partial response chunk..."}
data: {"sources": [{"fd_article": "url"}]}
data: {"full_text": "complete response", "session_id": "CKB-xxx"}
```

Enable via `?stream=true` query parameter.

## Logging

Two log streams in `logs/`:
- **access.log**: HTTP request/response tracking (JSON format, rotating 5MB, 3 backups)
  - Fields: requestId, method, path, status, responseTime, TTFB (for streaming)
- **debug.log**: Application debugging (standard format with timestamps)

Request IDs are auto-generated with `CKB-` prefix via `flask-request-id-header`.

## Fleet-Based Filtering

Fleet configurations define version constraints and metadata filters for Pinecone searches:

- **Fleet Portal Version**: Major.minor.patch matching (e.g., `v9.20.0`)
- **Device APK Version**: Major.minor matching (e.g., `v1.20.4`)
- **Camera Models**: Include/exclude specific models (e.g., `mitac-gemini`)
- **Plans**: SHIELD vs NON-SHIELD filtering
- **Features**: Required features filtering

Filters are constructed in `_get_file_search_filters()` and applied to Pinecone metadata queries.

## Document Loading Pipeline

1. **Freshdesk Sync** (`openai_vectorstore_sync.py`):
   - Fetches categories/folders/articles from Freshdesk API (async with aiohttp)
   - Filters by 12 allowed categories
   - Converts HTML → Markdown
   - Uploads to OpenAI Vector Store with metadata (article URL, ID)

2. **Local Document Loading** (`load_bulk_docs.py`):
   - Reads JSON files from `data/` directory
   - Creates HTML files with article content
   - Uploads to OpenAI Vector Store

3. **Categorized Loading** (`load_bulk_docs_categorised.py`):
   - Loads documents into category-specific collections
   - Supports 10 collections (internal, general, master_portal, fleet_portal, etc.)

## Citation Handling

Citations are collected asynchronously in separate threads to avoid blocking the main response stream. Citation patterns removed in post-processing:

- ANTML cite tags: `<cite>text</cite>`
- Bracket citations: `[1]`, `[2]`
- Chinese bracket citations: `【4:1†First.pdf】`
- Parenthetical citations: `(Author, 2024)`

File metadata is fetched via OpenAI Vector Store Files API.

## Development Patterns

### Adding a New RAG Handler

1. Create handler class in `rag_utils/` extending base response pattern
2. Implement `process_question(user_input, prev_response_id, instructions, context_args)`
3. Add to session initialization in `main.py` (`create_session_object()`)
4. Route traffic via endpoint query params (e.g., `?backend=new_handler`)

### Adding a New Tool Function

1. Define function schema in `tools/function_definitions.py`
2. Implement function in `tools/ai_tools.py`
3. Register in `AITools.call_tool_function()` dispatcher
4. Include in `create_tools_config()` tool list

### Adding a New Vector Store Collection

1. Add collection name to `utils/collection_config.py` `categories` dict
2. Create PGVector collection in PostgreSQL
3. Load documents via `load_bulk_docs_categorised.py` with category filter
4. Update retriever initialization in `rag_utils/rag_setup.py`

## Security Considerations

From `todo` file, note these security improvements needed:
- Use parameterized queries (`cur.execute(sql, data)`) to prevent SQL injection
- Connection pooling is implemented but review for edge cases
- Session cleanup for expired sessions (sessionObjectMap keys)

## Testing Streaming

Use the test endpoint to verify streaming functionality:
```bash
curl -X GET "http://localhost:5000/v2/llm-kb/test-streaming"
```

## Deployment

Production deployment via Docker with AWS Parameter Store integration:
1. Build: `docker build -t chat-knowledge-base:latest .`
2. Run: `./start_container.sh` pulls env vars from AWS Systems Manager Parameter Store
3. Filebeat is auto-started if `MONITORING_ES_HOST` is set (for centralized logging)

Required IAM permissions: `ssm:GetParametersByPath` with decryption for Parameter Store region/path.
