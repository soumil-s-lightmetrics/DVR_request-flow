# Pinecone Vector Store Sync

This script synchronizes Freshdesk knowledge base articles to Pinecone vector store with fleet-specific metadata filtering. It runs in an isolated virtual environment for independent deployment.

## Overview

The Pinecone sync script:
- Fetches articles from Freshdesk API
- Extracts metadata and attributes using GPT-4
- Chunks articles using LangChain text splitter
- Embeds content using OpenAI embeddings
- Stores vectors in Pinecone with fleet-specific metadata
- Tracks sync status in PostgreSQL database

## Directory Structure

```
pinecone_vectorstore_sync/
├── pinecone_vectorstore_sync.py    # Main sync script
├── logger.py                       # Logging utilities
├── utils/                          # Local utilities
│   ├── __init__.py
│   ├── attribute_parser.py         # Attribute parsing with GPT-4
│   └── pg_connections.py           # PostgreSQL connections
├── requirements.txt                # Python dependencies
├── .env.template                   # Environment variables template
├── README.md                       # This file
├── logs/                           # Log directory (created at runtime)
└── .gitignore                      # Git ignore rules
```

## Prerequisites

- Python 3.8 or higher
- Access to Pinecone, OpenAI, Freshdesk, and PostgreSQL
- API keys and credentials for all services

## Setup Instructions

### 1. Create Virtual Environment

```bash
# Navigate to script directory
cd scripts/pinecone_vectorstore_sync

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On Linux/Mac
# OR
.\venv\Scripts\activate  # On Windows
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

```bash
# Copy template
cp .env.template .env

# Edit .env with your credentials
nano .env  # or use your preferred editor
```

Required environment variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `PINECONE_API_KEY` | Pinecone API key | `pc-xxxxx` |
| `PINECONE_INDEX_HOST` | Pinecone index host | `your-index.pinecone.io` |
| `PINECONE_INDEX_NAME` | Pinecone index name | `knowledge-base` |
| `OPENAI_API_KEY` | OpenAI API key | `sk-xxxxx` |
| `FRESHDESK_API_BASE_URL` | Freshdesk domain | `https://yourcompany.freshdesk.com` |
| `FRESHDESK_API_KEY` | Freshdesk API key | `xxxxx` |
| `LLM_POSTGRES_DATABASENAME` | PostgreSQL database name | `lisa_db` |
| `LLM_POSTGRES_USERNAME` | PostgreSQL username | `postgres` |
| `LLM_POSTGRES_PASSWORD` | PostgreSQL password | `your_password` |
| `LLM_POSTGRES_HOST` | PostgreSQL host | `localhost` or IP |
| `LLM_POSTGRES_PORT` | PostgreSQL port | `5432` |
| `LLM_POSTGRES_POOL_SIZE` | Connection pool size | `10` |
| `LOG_TO_STDOUT` | Enable stdout logging | `true` or `false` |

## Usage

### Basic Usage

Sync all articles:

```bash
python pinecone_vectorstore_sync.py
```

### Sync Specific Articles

```bash
python pinecone_vectorstore_sync.py --article-ids 12345,67890,54321
```

### Force Re-sync

Re-sync all articles, ignoring sync status:

```bash
python pinecone_vectorstore_sync.py --force
```

### Combine Options

Force re-sync specific articles:

```bash
python pinecone_vectorstore_sync.py --article-ids 12345,67890 --force
```

## Command Line Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--article-ids` | Comma-separated list of article IDs to sync | All articles |
| `--force` | Force re-sync even if already synced | False |

## How It Works

1. **Fetch Articles**: Retrieves articles from Freshdesk API
2. **Parse Attributes**: Uses GPT-4 to extract fleet, vehicle, and tag metadata
3. **Chunk Content**: Splits articles into manageable chunks using LangChain
4. **Generate Embeddings**: Creates vector embeddings using OpenAI
5. **Store in Pinecone**: Uploads vectors with metadata to Pinecone
6. **Track Status**: Updates PostgreSQL sync status table

## Logging

Logs are written to:
- `logs/pinecone_sync.log` - File logging
- stdout (if `LOG_TO_STDOUT=true`)

Log levels:
- INFO: Normal operation progress
- WARNING: Non-critical issues
- ERROR: Failures and exceptions

## Database Tables

The script uses the following PostgreSQL tables:

### `pinecone_article_sync_status`

Tracks sync status for each article:

| Column | Type | Description |
|--------|------|-------------|
| `article_id` | BIGINT | Freshdesk article ID (primary key) |
| `last_synced_at` | TIMESTAMP | Last successful sync timestamp |
| `sync_status` | VARCHAR | Sync status (success, failed, pending) |
| `error_message` | TEXT | Error details if sync failed |

## Deployment

### Production Server

1. Copy entire directory to server:
   ```bash
   scp -r scripts/pinecone_vectorstore_sync/ user@server:/path/to/deployment/
   ```

2. SSH into server and set up:
   ```bash
   cd /path/to/deployment/pinecone_vectorstore_sync
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   cp .env.template .env
   # Edit .env with production credentials
   ```

3. Test the script:
   ```bash
   python pinecone_vectorstore_sync.py --article-ids 12345
   ```

### Cron Job

Schedule regular syncs:

```bash
# Edit crontab
crontab -e

# Add cron job (runs daily at 2 AM)
0 2 * * * cd /path/to/deployment/pinecone_vectorstore_sync && ./venv/bin/python pinecone_vectorstore_sync.py >> logs/cron.log 2>&1
```

### Systemd Service

Create a systemd service for automatic startup:

```ini
[Unit]
Description=Pinecone Vector Store Sync
After=network.target

[Service]
Type=oneshot
User=your_user
WorkingDirectory=/path/to/deployment/pinecone_vectorstore_sync
Environment="PATH=/path/to/deployment/pinecone_vectorstore_sync/venv/bin"
ExecStart=/path/to/deployment/pinecone_vectorstore_sync/venv/bin/python pinecone_vectorstore_sync.py
StandardOutput=append:/path/to/deployment/pinecone_vectorstore_sync/logs/service.log
StandardError=append:/path/to/deployment/pinecone_vectorstore_sync/logs/service.log

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

### Import Errors

```bash
# Verify virtual environment is activated
which python  # Should point to venv/bin/python

# Test imports
python -c "from logger import configure_logger; from utils.attribute_parser import get_attributes_from_tags; from utils.pg_connections import get_connection_url_pg; print('Imports successful!')"
```

### Database Connection Issues

- Verify PostgreSQL credentials in `.env`
- Check network connectivity to database host
- Ensure database exists and user has permissions
- Verify `pinecone_article_sync_status` table exists

### Pinecone API Errors

- Verify `PINECONE_API_KEY` is correct
- Check `PINECONE_INDEX_HOST` matches your index
- Ensure index exists and is active
- Verify index dimension matches embedding size (1536 for OpenAI)

### OpenAI API Errors

- Verify `OPENAI_API_KEY` is valid
- Check API usage limits and billing
- Monitor rate limits in logs

### Freshdesk API Errors

- Verify `FRESHDESK_API_KEY` and base URL
- Check API rate limits
- Ensure article IDs are valid

## Performance Considerations

- **Batch Size**: Script processes articles sequentially for reliability
- **Rate Limits**: Respects OpenAI and Freshdesk API rate limits
- **Memory Usage**: Chunking prevents memory issues with large articles
- **Concurrency**: Uses async operations for database and API calls

## Maintenance

### Update Dependencies

```bash
source venv/bin/activate
pip install --upgrade -r requirements.txt
```

### Clear Sync Status

To force complete re-sync:

```sql
-- Connect to PostgreSQL
TRUNCATE TABLE pinecone_article_sync_status;
```

Or use the `--force` flag.

### Monitor Logs

```bash
# Tail logs in real-time
tail -f logs/pinecone_sync.log

# Search for errors
grep ERROR logs/pinecone_sync.log

# View recent syncs
grep "Successfully synced" logs/pinecone_sync.log | tail -20
```

## Architecture

### Dependencies

- **pinecone**: Vector database client
- **openai**: LLM and embeddings API
- **langchain**: Text chunking utilities
- **asyncpg**: Async PostgreSQL client
- **aiohttp**: Async HTTP client
- **python-dotenv**: Environment variable management
- **html-to-markdown**: HTML conversion
- **tqdm**: Progress indicators
- **pydantic**: Data validation

### Component Interaction

```
[Freshdesk API] → [Article Fetcher] → [GPT-4 Attribute Parser]
                                              ↓
[PostgreSQL] ← [Sync Status Tracker] ← [Pinecone Uploader]
                                              ↑
                                    [OpenAI Embeddings]
```

## Support

For issues or questions:
1. Check logs in `logs/pinecone_sync.log`
2. Review this README for troubleshooting steps
3. Contact the development team

## Version History

- **v1.0.0** (2026-01-16): Initial isolated deployment setup
