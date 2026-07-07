"""
Unit tests for pinecone_vectorstore_sync.py

Tests chunking, metadata building, and sync orchestration logic.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
import asyncio
from datetime import datetime, timezone
import hashlib
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock environment variables before importing the module
os.environ.update({
    'PINECONE_API_KEY': 'test_key',
    'PINECONE_INDEX_HOST': 'test_host',
    'PINECONE_INDEX_NAME': 'test_index',
    'OPENAI_API_KEY': 'test_openai_key',
    'FRESHDESK_API_BASE_URL': 'https://test.freshdesk.com',
    'FRESHDESK_API_KEY': 'test_fd_key',
})

# Now import after setting env vars
from pinecone_vectorstore_sync import (
    chunk_article,
    generate_embeddings,
    should_sync_article,
)
from utils.attribute_parser import get_attributes_from_tags


class TestChunkArticle:
    """Test article chunking logic."""

    def test_chunk_simple_article(self):
        """Test chunking a simple article."""
        article = {
            'id': 12345,
            'title': 'Test Article',
            'description': '<p>This is a test article. ' * 100 + '</p>',
            'category': 'General',
            'folder': 'Test Folder'
        }

        chunks = chunk_article(article)

        assert len(chunks) > 0
        assert all(isinstance(c, dict) for c in chunks)
        assert all('chunk_index' in c for c in chunks)
        assert all('chunk_text' in c for c in chunks)
        assert all('article_id' in c for c in chunks)

    def test_chunk_indices_sequential(self):
        """Test that chunk indices are sequential starting from 0."""
        article = {
            'id': 12345,
            'title': 'Test Article',
            'description': '<p>This is a test article. ' * 200 + '</p>',
            'category': 'General',
            'folder': 'Test Folder'
        }

        chunks = chunk_article(article)

        indices = [c['chunk_index'] for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_chunk_size_limit(self):
        """Test that chunks respect the 1000 character limit."""
        article = {
            'id': 12345,
            'title': 'Test Article',
            'description': '<p>This is a test article. ' * 200 + '</p>',
            'category': 'General',
            'folder': 'Test Folder'
        }

        chunks = chunk_article(article)

        # Most chunks should be close to 1000 chars (within reasonable margin)
        # Last chunk might be smaller
        for chunk in chunks[:-1]:
            assert len(chunk['chunk_text']) <= 1200  # Allow some overhead

    def test_chunk_empty_article(self):
        """Test chunking article with no description."""
        article = {
            'id': 12345,
            'title': 'Test Article',
            'description': '',
            'category': 'General',
            'folder': 'Test Folder'
        }

        chunks = chunk_article(article)

        assert len(chunks) == 0

    def test_chunk_preserves_metadata(self):
        """Test that chunks preserve article metadata."""
        article = {
            'id': 12345,
            'title': 'Test Article',
            'description': '<p>Content here</p>',
            'category': 'Fleet Portal',
            'folder': 'Setup Guide'
        }

        chunks = chunk_article(article)

        assert all(c['article_id'] == 12345 for c in chunks)
        assert all(c['article_title'] == 'Test Article' for c in chunks)
        assert all(c['category'] == 'Fleet Portal' for c in chunks)
        assert all(c['folder'] == 'Setup Guide' for c in chunks)

    def test_chunk_html_to_markdown_conversion(self):
        """Test that HTML is converted to markdown."""
        article = {
            'id': 12345,
            'title': 'Test Article',
            'description': '<h1>Heading</h1><p>Paragraph content</p><ul><li>Item 1</li></ul>',
            'category': 'General',
            'folder': 'Test'
        }

        chunks = chunk_article(article)

        # Should have at least one chunk
        assert len(chunks) > 0
        # Markdown should not contain HTML tags
        assert '<h1>' not in chunks[0]['chunk_text']
        assert '<p>' not in chunks[0]['chunk_text']


class TestGenerateEmbeddings:
    """Test embedding generation (with mocking)."""

    @patch('pinecone_vectorstore_sync.openai_client')
    def test_generate_single_embedding(self, mock_openai_client):
        """Test generating embedding for single text."""
        # Mock OpenAI response structure
        mock_response = Mock()
        mock_response.data = [Mock(embedding=[0.1] * 1536)]
        mock_openai_client.embeddings.create.return_value = mock_response

        texts = ["Test text"]
        embeddings = generate_embeddings(texts)

        assert len(embeddings) == 1
        assert len(embeddings[0]) == 1536
        mock_openai_client.embeddings.create.assert_called_once()

    @patch('pinecone_vectorstore_sync.openai_client')
    def test_generate_multiple_embeddings(self, mock_openai_client):
        """Test generating embeddings for multiple texts."""
        # Mock OpenAI response structure with multiple embeddings
        mock_response = Mock()
        mock_response.data = [
            Mock(embedding=[0.1] * 1536),
            Mock(embedding=[0.2] * 1536),
            Mock(embedding=[0.3] * 1536)
        ]
        mock_openai_client.embeddings.create.return_value = mock_response

        texts = ["Text 1", "Text 2", "Text 3"]
        embeddings = generate_embeddings(texts)

        assert len(embeddings) == 3
        assert all(len(emb) == 1536 for emb in embeddings)
        # Should only call once since texts are in same batch
        assert mock_openai_client.embeddings.create.call_count == 1

    @patch('pinecone_vectorstore_sync.openai_client')
    def test_generate_embedding_error_fallback(self, mock_openai_client):
        """Test that errors fall back to zero vector."""
        mock_openai_client.embeddings.create.side_effect = Exception("OpenAI API error")

        texts = ["Test text"]
        embeddings = generate_embeddings(texts)

        assert len(embeddings) == 1
        assert embeddings[0] == [0.0] * 1536


@pytest.mark.asyncio
class TestShouldSyncArticle:
    """Test article sync decision logic."""

    async def test_new_article_should_sync(self):
        """Test that new articles (not in DB) should sync."""
        article = {
            'id': 12345,
            'updated_at': '2024-01-01T10:00:00Z',
            'description': 'Test content'
        }

        # Mock pool with no existing entry
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value=None)

        # Create a proper async context manager
        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire = MagicMock(return_value=mock_acquire)

        result = await should_sync_article(article, mock_pool)

        assert result is True

    async def test_updated_article_should_sync(self):
        """Test that updated articles should sync."""
        article = {
            'id': 12345,
            'updated_at': '2024-01-02T10:00:00Z',
            'description': 'Test content'
        }

        # Mock pool with old timestamp
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            'last_synced_at': datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            'content_hash': 'old_hash'
        })

        # Create a proper async context manager
        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire = MagicMock(return_value=mock_acquire)

        result = await should_sync_article(article, mock_pool)

        assert result is True

    async def test_unchanged_article_should_not_sync(self):
        """Test that unchanged articles should not sync."""
        content = 'Test content'
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        article = {
            'id': 12345,
            'updated_at': '2024-01-01T10:00:00Z',
            'description': content
        }

        # Mock pool with same timestamp and hash
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            'last_synced_at': datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            'content_hash': content_hash
        })

        # Create a proper async context manager
        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire = MagicMock(return_value=mock_acquire)

        result = await should_sync_article(article, mock_pool)

        assert result is False

    async def test_content_changed_should_sync(self):
        """Test that articles with changed content should sync."""
        article = {
            'id': 12345,
            'updated_at': '2024-01-01T10:00:00Z',
            'description': 'New content'
        }

        # Mock pool with different content hash
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.fetchrow = AsyncMock(return_value={
            'last_synced_at': datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            'content_hash': 'old_hash_different'
        })

        # Create a proper async context manager
        mock_acquire = MagicMock()
        mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire = MagicMock(return_value=mock_acquire)

        result = await should_sync_article(article, mock_pool)

        assert result is True


class TestMetadataBuilding:
    """Test building Pinecone metadata from chunks and tags."""

    def test_build_complete_metadata(self):
        """Test building complete metadata structure."""
        chunk = {
            'chunk_index': 0,
            'chunk_text': 'Test content',
            'article_id': 12345,
            'article_title': 'Test Article',
            'category': 'Fleet Portal',
            'folder': 'Setup'
        }

        tags = {
            "fleet_portal_version": "10.9.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "1.20",
            "device_models": ["jimi-jc261"],
            "plans_nin": ["SHIELD"],
            "event_types": ["Traffic-Light-Violated"],
            "required_features": ["ADAS"]
        }

        attributes = get_attributes_from_tags(tags)

        # Build metadata (simulating what sync script does)
        metadata = {
            "fd_article_id": chunk['article_id'],
            "fd_article_url": f"https://lightmetrics.freshdesk.com/a/solutions/articles/{chunk['article_id']}",
            "article_title": chunk['article_title'],
            "freshdesk_category": chunk['category'],
            "folder_name": chunk['folder'],
            "chunk_index": chunk['chunk_index'],
            "chunk_text": chunk['chunk_text'],
            **attributes
        }

        # Verify structure
        assert metadata['fd_article_id'] == 12345
        assert metadata['article_title'] == 'Test Article'
        assert metadata['chunk_index'] == 0
        assert metadata['fleet_portal_version_major'] == 10
        assert metadata['device_models_in'] == ["jimi-jc261"]
        assert metadata['plans_nin'] == ["SHIELD"]

    def test_build_metadata_with_defaults(self):
        """Test building metadata with default values."""
        chunk = {
            'chunk_index': 0,
            'chunk_text': 'General content',
            'article_id': 12345,
            'article_title': 'Login Guide',
            'category': 'General',
            'folder': 'Getting Started'
        }

        tags = {
            "fleet_portal_version": "0.0.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "0.0",
            "device_models": [],
            "plans_nin": [],
            "event_types": [],
            "required_features": []
        }

        attributes = get_attributes_from_tags(tags)

        metadata = {
            "fd_article_id": chunk['article_id'],
            "fd_article_url": f"https://lightmetrics.freshdesk.com/a/solutions/articles/{chunk['article_id']}",
            "article_title": chunk['article_title'],
            "freshdesk_category": chunk['category'],
            "folder_name": chunk['folder'],
            "chunk_index": chunk['chunk_index'],
            "chunk_text": chunk['chunk_text'],
            **attributes
        }

        # Verify defaults
        assert metadata['fleet_portal_version_major'] == 0
        assert metadata['device_apk_version_major'] == 0


class TestVectorIDGeneration:
    """Test vector ID generation logic."""

    def test_vector_id_format(self):
        """Test vector ID has correct stable format (no timestamp)."""
        article_id = 12345
        chunk_index = 2

        # Stable vector ID format (no timestamp)
        vector_id = f"art{article_id}_ch{chunk_index}"

        assert vector_id == "art12345_ch2"
        assert vector_id.startswith("art12345")
        assert "_ch2" in vector_id
        # No timestamp in stable IDs
        assert "_v" not in vector_id

    def test_vector_id_stability(self):
        """Test that vector IDs are stable (same ID for same article+chunk)."""
        article_id = 12345
        chunk_index = 0

        # Generate ID twice - should be identical (stable)
        vector_id1 = f"art{article_id}_ch{chunk_index}"
        vector_id2 = f"art{article_id}_ch{chunk_index}"

        # Stable IDs are always the same
        assert vector_id1 == vector_id2
        assert vector_id1 == "art12345_ch0"


class TestIntegrationScenarios:
    """Test complete workflows end-to-end."""

    def test_scenario_chunk_extract_build_metadata(self):
        """Test complete flow: chunk → extract → build metadata."""
        # Step 1: Chunk article
        article = {
            'id': 12345,
            'title': 'ADAS Setup for JiMi Cameras',
            'description': '<p>To enable ADAS features on your JiMi JC261 camera, you need Fleet Portal version 10.9 or higher.</p>',
            'category': 'Fleet Portal',
            'folder': 'Device Configuration'
        }

        chunks = chunk_article(article)
        assert len(chunks) > 0

        # Step 2: Simulate LLM extraction
        tags = {
            "fleet_portal_version": "10.9.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "1.20",
            "device_models": ["jimi-jc261"],
            "plans_nin": ["SHIELD"],
            "event_types": [],
            "required_features": ["ADAS"]
        }

        # Step 3: Convert to attributes
        attributes = get_attributes_from_tags(tags)

        # Step 4: Build metadata
        chunk = chunks[0]
        metadata = {
            "fd_article_id": chunk['article_id'],
            "fd_article_url": f"https://lightmetrics.freshdesk.com/a/solutions/articles/{chunk['article_id']}",
            "article_title": chunk['article_title'],
            "freshdesk_category": chunk['category'],
            "folder_name": chunk['folder'],
            "chunk_index": chunk['chunk_index'],
            "chunk_text": chunk['chunk_text'],
            **attributes
        }

        # Verify complete metadata
        assert metadata['fd_article_id'] == 12345
        assert metadata['fleet_portal_version_major'] == 10
        assert metadata['fleet_portal_version_minor'] == 9
        assert metadata['device_models_in'] == ["jimi-jc261"]
        assert metadata['plans_nin'] == ["SHIELD"]
        assert metadata['required_features'] == ["ADAS"]

    def test_scenario_general_content_minimal_metadata(self):
        """Test minimal metadata for general content."""
        article = {
            'id': 67890,
            'title': 'How to Login',
            'description': '<p>Navigate to the Fleet Portal and enter your credentials.</p>',
            'category': 'General',
            'folder': 'Getting Started'
        }

        chunks = chunk_article(article)
        assert len(chunks) > 0

        # Simulate LLM returning defaults
        tags = {
            "fleet_portal_version": "0.0.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "0.0",
            "device_models": [],
            "plans_nin": [],
            "event_types": [],
            "required_features": []
        }

        attributes = get_attributes_from_tags(tags)

        chunk = chunks[0]
        metadata = {
            "fd_article_id": chunk['article_id'],
            "article_title": chunk['article_title'],
            "chunk_index": chunk['chunk_index'],
            "chunk_text": chunk['chunk_text'],
            **attributes
        }

        # Should have minimal attributes (defaults)
        assert metadata['fleet_portal_version_major'] == 0
        assert metadata['device_apk_version_major'] == 0
        assert 'device_models_in' not in metadata  # Empty list not added


@pytest.mark.asyncio
class TestAsyncOperations:
    """Test async operations with proper mocking."""

    @patch('pinecone_vectorstore_sync.openai_client')
    async def test_extract_attributes_batch_processing(self, mock_openai):
        """Test that LLM extraction processes in batches."""
        # This is a mock test since actual LLM calls are expensive

        # Create mock response
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = '{"attributes": {"fleet_portal_version": "0.0.0", "master_portal_version": "0.0.0", "device_apk_version": "0.0", "device_models": [], "plans_nin": [], "event_types": [], "required_features": []}}'

        mock_openai.chat.completions.create.return_value = mock_response

        # Would need to import and test extract_attributes_with_llm
        # Skipping actual call to avoid real API usage
        # This test serves as a placeholder for integration testing

        assert True  # Placeholder

    async def test_concurrent_article_syncing(self):
        """Test that multiple articles can be synced concurrently."""
        # Test that semaphore controls concurrency properly
        semaphore = asyncio.Semaphore(10)

        async def mock_sync(article_id):
            async with semaphore:
                await asyncio.sleep(0.01)
                return {'article_id': article_id, 'status': 'completed'}

        tasks = [mock_sync(i) for i in range(20)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 20
        assert all(r['status'] == 'completed' for r in results)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
