"""
Data collector for RAG evaluation.

Collects inference data from RAG handlers for evaluation with ragas.
"""

import json
import os
import time
import logging
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from rag_utils.openai_responses_rag import OpenAIResponsesHandler
from rag_utils.pinecone_openai_rag import PineconeOpenAIResponsesHandler
from assistant_rag import AssistantUtil
from utils.s3_config_manager import S3ConfigManager
from utils.fleet_config_manager import FleetConfigManager
from utils.freshdesk_api_util import FreshdeskAPIUtil
from utils.prompts import lisa_main_system_prompt, fleet_lisa_main_system_prompt
from evaluation.config import EvaluationConfig


class RAGDataCollector:
    """
    Collects inference data from RAG handlers for evaluation.

    Handles streaming responses from both OpenAI and Pinecone handlers,
    extracting answers, contexts, and metadata for ragas evaluation.
    """

    def __init__(
        self,
        config: EvaluationConfig,
        config_manager: S3ConfigManager,
        logger: Optional[logging.Logger] = None
    ):
        """
        Initialize data collector.

        Args:
            config: Evaluation configuration
            config_manager: S3 config manager for handlers
            logger: Optional logger instance
        """
        self.config = config
        self.config_manager = config_manager
        self.logger = logger or logging.getLogger(__name__)

        # Initialize FleetConfigManager for Pinecone handler
        self.fleet_config_manager = FleetConfigManager(
            ttl_seconds=int(os.getenv("FLEET_CONFIG_CACHE_TTL", "3600")),
            use_dynamic_config=os.getenv("FLEET_CONFIG_USE_DYNAMIC", "true").lower() == "true"
        )

        # Initialize Freshdesk API util for fetching article content
        try:
            self.freshdesk_util = FreshdeskAPIUtil()
        except ValueError as e:
            self.logger.warning(f"Freshdesk API util not initialized: {e}")
            self.freshdesk_util = None

        # Cache for article content
        self._article_cache = {}

        # Load ground truth
        self.ground_truth = self._load_ground_truth()

        # Load golden questions
        self.questions = self._load_golden_questions()

    def _load_ground_truth(self) -> Dict[str, Any]:
        """Load ground truth data from JSON file."""
        try:
            with open(self.config.ground_truth_path, 'r') as f:
                data = json.load(f)
            # Remove metadata fields (those starting with _)
            return {k: v for k, v in data.items() if not k.startswith('_')}
        except FileNotFoundError:
            self.logger.warning(f"Ground truth file not found: {self.config.ground_truth_path}")
            return {}
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing ground truth JSON: {e}")
            return {}

    def _get_ground_truth_for_question(
        self,
        question: str,
        fleet_config_name: Optional[str]
    ) -> str:
        """
        Get ground truth answer for a question, considering fleet configuration.

        Args:
            question: The question
            fleet_config_name: Fleet config name (e.g., 'shield', 'non-shield', 'no-fleet', None)

        Returns:
            Ground truth answer string
        """
        question_data = self.ground_truth.get(question, {})

        if not question_data:
            return ""

        # Normalize fleet config name
        config_key = fleet_config_name if fleet_config_name else "no-fleet"

        # Try fleet-specific ground truth first
        if config_key in question_data:
            return question_data[config_key].get("ground_truth", "")

        # Fall back to "default" if available
        if "default" in question_data:
            return question_data["default"].get("ground_truth", "")

        # If old format (direct ground_truth field), use it
        if "ground_truth" in question_data:
            return question_data.get("ground_truth", "")

        return ""

    def _load_golden_questions(self) -> List[str]:
        """Load golden questions from JSON file."""
        try:
            with open(self.config.golden_questions_path, 'r') as f:
                questions = json.load(f)
            self.logger.info(f"Loaded {len(questions)} golden questions")
            return questions
        except FileNotFoundError:
            self.logger.error(f"Golden questions file not found: {self.config.golden_questions_path}")
            return []
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing golden questions JSON: {e}")
            return []

    def _get_article_content(self, article_id: str) -> Optional[str]:
        """
        Fetch article content from Freshdesk API with caching.

        Args:
            article_id: The Freshdesk article ID

        Returns:
            Article description_text (plain text content) or None if failed
        """
        if not self.freshdesk_util:
            return None

        if article_id in self._article_cache:
            return self._article_cache[article_id]

        try:
            # Run async method synchronously
            article_data = asyncio.run(self.freshdesk_util.get_article_details(article_id))
            # Use description_text for plain text content
            content = article_data.get('description_text', '')
            self._article_cache[article_id] = content
            return content
        except Exception as e:
            self.logger.error(f"Error fetching article {article_id}: {e}")
            return None

    def collect_openai_handler_data(
        self,
        handler: OpenAIResponsesHandler,
        fleet_config: Optional[Dict[str, Any]],
        questions: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Collect data from OpenAIResponsesHandler.

        Args:
            handler: OpenAI handler instance
            fleet_config: Fleet configuration (should be None for OpenAI)
            questions: List of questions to evaluate (defaults to golden questions)

        Returns:
            List of evaluation data points
        """
        questions = questions or self.questions
        results = []

        self.logger.info(f"Collecting data from OpenAI handler for {len(questions)} questions")

        for i, question in enumerate(questions, 1):
            self.logger.info(f"Processing question {i}/{len(questions)}: {question[:50]}...")

            try:
                start_time = time.time()

                # OpenAI handler always uses default prompt (no fleet-specific instructions)
                instructions = lisa_main_system_prompt

                # Process question with handler
                stream_generator = handler.process_question(
                    question=question,
                    prev_resp_id=None,
                    instructions=instructions,
                    fleet_config=fleet_config
                )

                # Extract data from streaming response
                answer, contexts, references, metadata = self._extract_from_openai_stream(
                    stream_generator,
                    handler
                )

                latency_ms = (time.time() - start_time) * 1000

                # Get ground truth (OpenAI handler always uses no-fleet)
                ground_truth = self._get_ground_truth_for_question(question, None)

                # Build data point
                data_point = {
                    "question": question,
                    "answer": answer,
                    "contexts": contexts,
                    "ground_truth": ground_truth,
                    "references": references,
                    "metadata": {
                        "handler": "oai-responses",
                        "fleet_config": None,
                        "latency_ms": latency_ms,
                        "total_tokens": metadata.get("usage", {}).get("total_tokens", 0),
                        "timestamp": datetime.now().isoformat()
                    }
                }

                results.append(data_point)

                self.logger.info(f"✓ Question {i} completed in {latency_ms:.0f}ms")

            except Exception as e:
                self.logger.error(f"✗ Error processing question {i}: {str(e)}")
                # Add error data point
                results.append({
                    "question": question,
                    "answer": "",
                    "contexts": [],
                    "ground_truth": self._get_ground_truth_for_question(question, None),
                    "references": [],
                    "metadata": {
                        "handler": "oai-responses",
                        "fleet_config": None,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat()
                    }
                })

        return results

    def collect_pinecone_handler_data(
        self,
        handler: PineconeOpenAIResponsesHandler,
        fleet_config: Optional[Dict[str, Any]],
        fleet_config_name: Optional[str],
        questions: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Collect data from PineconeOpenAIResponsesHandler.

        Args:
            handler: Pinecone handler instance
            fleet_config: Fleet configuration dict
            fleet_config_name: Fleet config name for metadata
            questions: List of questions to evaluate (defaults to golden questions)

        Returns:
            List of evaluation data points
        """
        questions = questions or self.questions
        results = []

        config_desc = fleet_config_name or "no-fleet"
        self.logger.info(f"Collecting data from Pinecone handler ({config_desc}) for {len(questions)} questions")

        for i, question in enumerate(questions, 1):
            self.logger.info(f"Processing question {i}/{len(questions)}: {question[:50]}...")

            try:
                start_time = time.time()

                # Set instructions based on fleet_config
                if fleet_config:
                    instructions = fleet_lisa_main_system_prompt(fleet_config)
                else:
                    instructions = lisa_main_system_prompt

                # Process question with handler
                stream_generator = handler.process_question(
                    question=question,
                    prev_resp_id=None,
                    instructions=instructions,
                    fleet_config=fleet_config
                )

                # Extract data from streaming response
                answer, contexts, sources, metadata = self._extract_from_pinecone_stream(
                    stream_generator,
                    handler,
                    question,
                    fleet_config
                )

                latency_ms = (time.time() - start_time) * 1000

                # Get fleet-specific ground truth
                ground_truth = self._get_ground_truth_for_question(question, fleet_config_name)

                # Build data point
                data_point = {
                    "question": question,
                    "answer": answer,
                    "contexts": contexts,
                    "ground_truth": ground_truth,
                    "references": sources,
                    "metadata": {
                        "handler": "pinecone-oai",
                        "fleet_config": fleet_config_name,
                        "latency_ms": latency_ms,
                        "total_tokens": metadata.get("usage", {}).get("total_tokens", 0),
                        "timestamp": datetime.now().isoformat()
                    }
                }

                results.append(data_point)

                self.logger.info(f"✓ Question {i} completed in {latency_ms:.0f}ms")

            except Exception as e:
                self.logger.error(f"✗ Error processing question {i}: {str(e)}")
                # Add error data point
                results.append({
                    "question": question,
                    "answer": "",
                    "contexts": [],
                    "ground_truth": self._get_ground_truth_for_question(question, fleet_config_name),
                    "references": [],
                    "metadata": {
                        "handler": "pinecone-oai",
                        "fleet_config": fleet_config_name,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat()
                    }
                })

        return results

    def collect_assistant_handler_data(
        self,
        handler: AssistantUtil,
        fleet_config: Optional[Dict[str, Any]],
        questions: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Collect data from AssistantUtil handler.

        Args:
            handler: AssistantUtil instance
            fleet_config: Fleet configuration (should be None for Assistant)
            questions: List of questions to evaluate (defaults to golden questions)

        Returns:
            List of evaluation data points
        """
        questions = questions or self.questions
        results = []

        self.logger.info(f"Collecting data from OpenAI Assistant handler for {len(questions)} questions")

        for i, question in enumerate(questions, 1):
            self.logger.info(f"Processing question {i}/{len(questions)}: {question[:50]}...")

            try:
                start_time = time.time()

                # Generate a unique session_id for each question
                session_id = f"eval_session_{i}_{int(time.time())}"

                # Process question with handler
                stream_generator = handler.stream_process_query(
                    session_id=session_id,
                    query=question
                )

                # Extract data from streaming response
                answer, contexts, references, metadata = self._extract_from_assistant_stream(
                    stream_generator,
                    handler
                )

                latency_ms = (time.time() - start_time) * 1000

                # Get ground truth (Assistant handler always uses no-fleet)
                ground_truth = self._get_ground_truth_for_question(question, None)

                # Build data point
                data_point = {
                    "question": question,
                    "answer": answer,
                    "contexts": contexts,
                    "ground_truth": ground_truth,
                    "references": references,
                    "metadata": {
                        "handler": "oai-assistant",
                        "fleet_config": None,
                        "latency_ms": latency_ms,
                        "session_id": session_id,
                        "timestamp": datetime.now().isoformat()
                    }
                }

                results.append(data_point)

                self.logger.info(f"✓ Question {i} completed in {latency_ms:.0f}ms")

            except Exception as e:
                self.logger.error(f"✗ Error processing question {i}: {str(e)}")
                # Add error data point
                results.append({
                    "question": question,
                    "answer": "",
                    "contexts": [],
                    "ground_truth": self._get_ground_truth_for_question(question, None),
                    "references": [],
                    "metadata": {
                        "handler": "oai-assistant",
                        "fleet_config": None,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat()
                    }
                })

        return results

    def _extract_from_openai_stream(
        self,
        stream_generator,
        handler: OpenAIResponsesHandler
    ) -> Tuple[str, List[str], List[str], Dict[str, Any]]:
        """
        Extract data from OpenAI streaming response using the complete event.

        Args:
            stream_generator: Streaming response generator
            handler: Handler instance for fetching citations

        Returns:
            Tuple of (answer, contexts, references, metadata)
        """
        full_text = ""
        references = []
        contexts = []
        metadata = {}

        try:
            for event_line in stream_generator:
                try:
                    event = json.loads(event_line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("event", "")

                # Extract everything from the complete event
                if event_type == "complete":
                    data = event.get("data", {})
                    full_text = data.get("full_text", "")
                    citations = data.get("references", [])

                    # Build metadata from usage
                    metadata = {
                        "usage": {
                            "total_tokens": data.get("usage", 0)
                        }
                    }

                    # Extract contexts and references from citations
                    # References structure: [{"id": id, "fd_article_url": source}, ...]
                    for citation in citations:
                        if isinstance(citation, dict):
                            # Extract article ID and URL
                            article_id = citation.get("id", "")
                            fd_article_url = citation.get("fd_article_url", "")

                            if fd_article_url:
                                references.append(fd_article_url)

                            # Fetch full article content from Freshdesk API as context
                            if article_id:
                                article_content = self._get_article_content(str(article_id))
                                if article_content:
                                    contexts.append(article_content)

                    break  # Complete event is the last one we need

                elif event_type == handler.EVENT_ERROR:
                    self.logger.error(f"Stream error: {event.get('data', {})}")

        except Exception as e:
            self.logger.error(f"Error processing OpenAI stream: {e}")

        return full_text, contexts, references, metadata

    def _extract_from_pinecone_stream(
        self,
        stream_generator,
        handler: PineconeOpenAIResponsesHandler,
        question: str,
        fleet_config: Optional[Dict[str, Any]]
    ) -> Tuple[str, List[str], List[str], Dict[str, Any]]:
        """
        Extract data from Pinecone streaming response using the complete event.

        Args:
            stream_generator: Streaming response generator
            handler: Pinecone handler instance
            question: Original question
            fleet_config: Fleet configuration

        Returns:
            Tuple of (answer, contexts, sources, metadata)
        """
        full_text = ""
        sources = []
        metadata = {}

        try:
            for event_line in stream_generator:
                try:
                    event = json.loads(event_line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("event", "")

                # Extract everything from the complete event
                if event_type == "complete":
                    data = event.get("data", {})
                    full_text = data.get("full_text", "")
                    citations = data.get("references", [])

                    # Build metadata from usage
                    metadata = {
                        "usage": {
                            "total_tokens": data.get("usage", 0)
                        }
                    }

                    # Extract sources from citations
                    # References structure: [{"id": id, "fd_article_url": source}, ...]
                    for citation in citations:
                        if isinstance(citation, dict):
                            fd_article_url = citation.get("fd_article_url", "")
                            if fd_article_url:
                                sources.append(fd_article_url)

                    break  # Complete event is the last one we need

                elif event_type == handler.EVENT_ERROR:
                    self.logger.error(f"Stream error: {event.get('data', {})}")

        except Exception as e:
            self.logger.error(f"Error processing Pinecone stream: {e}")

        # Get contexts directly from handler's get_context method
        contexts = []
        try:
            context_text, context_sources = handler.get_context(
                query=question,
                fleet_config=fleet_config
            )

            # Split context text into chunks (separated by newlines)
            if context_text:
                contexts = [chunk.strip() for chunk in context_text.split("\n\n") if chunk.strip()]

            # # Use context_sources only if sources not available from complete event
            # if not sources and context_sources:
            #     sources = context_sources

        except Exception as e:
            self.logger.error(f"Error getting context from Pinecone handler: {e}")

        return full_text, contexts, sources, metadata

    def _extract_from_assistant_stream(
        self,
        stream_generator,
        handler: AssistantUtil
    ) -> Tuple[str, List[str], List[str], Dict[str, Any]]:
        """
        Extract data from AssistantUtil streaming response using the complete event.

        Args:
            stream_generator: Streaming response generator
            handler: AssistantUtil instance

        Returns:
            Tuple of (answer, contexts, references, metadata)
        """
        full_text = ""
        references = []
        contexts = []
        metadata = {}

        try:
            for event_line in stream_generator:
                try:
                    event = json.loads(event_line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("event", "")

                # Extract everything from the complete event
                if event_type == "complete":
                    data = event.get("data", {})
                    full_text = data.get("full_text", "")
                    citations = data.get("references", [])

                    # Extract contexts and references from citations
                    # References structure: [{"id": id, "fd_article_url": source, ...}, ...]
                    for citation in citations:
                        if isinstance(citation, dict):
                            # Extract article ID and URL
                            article_id = citation.get("id", "")
                            fd_article_url = citation.get("fd_article_url", "")

                            if fd_article_url:
                                references.append(fd_article_url)

                            # Fetch full article content from Freshdesk API as context
                            if article_id:
                                article_content = self._get_article_content(str(article_id))
                                if article_content:
                                    contexts.append(article_content)

                    break  # Complete event is the last one we need

                elif event_type == "error":
                    self.logger.error(f"Stream error: {event.get('data', {})}")

        except Exception as e:
            self.logger.error(f"Error processing Assistant stream: {e}")

        return full_text, contexts, references, metadata

    def run_collection(
        self,
        output_dir: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Run data collection for all configured handlers and fleet configs.

        Args:
            output_dir: Output directory for collected data

        Returns:
            Dict mapping handler+config to output file path
        """
        output_dir = output_dir or self.config.collected_data_dir
        os.makedirs(output_dir, exist_ok=True)

        output_files = {}
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Collect for each enabled handler
        for handler_name in self.config.get_enabled_handlers():
            handler_config = self.config.handlers[handler_name]

            # Initialize handler
            self.logger.info(f"\n{'='*60}")
            self.logger.info(f"Initializing {handler_name} handler")
            self.logger.info(f"{'='*60}")

            try:
                # Get handler class
                if handler_name == "oai-responses":
                    handler = OpenAIResponsesHandler(
                        api_key=os.environ.get("OPENAI_API_KEY"),
                        vecstore_id=os.environ.get("OPENAI_VEC_STORE_ID"),
                        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
                        config_manager=self.config_manager
                    )
                elif handler_name == "pinecone-oai":
                    handler = PineconeOpenAIResponsesHandler(
                        api_key=os.environ.get("OPENAI_API_KEY"),
                        vecstore_id=os.environ.get("OPENAI_VEC_STORE_ID"),
                        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
                        config_manager=self.config_manager,
                        fleet_config_manager=self.fleet_config_manager
                    )
                elif handler_name == "oai-assistant":
                    handler = AssistantUtil(
                        api_key=os.environ.get("OPENAI_API_KEY"),
                        assistant_id=os.environ.get("OPENAI_ASSISTANT_ID"),
                        vecstore_id=os.environ.get("OPENAI_VEC_STORE_ID")
                    )
                else:
                    self.logger.error(f"Unknown handler: {handler_name}")
                    continue

                # Collect for each fleet config
                for fleet_config_name in handler_config.fleet_configs:
                    fleet_config = self.config.get_fleet_config(fleet_config_name)
                    config_desc = fleet_config_name or "no-fleet"

                    self.logger.info(f"\nCollecting data for {handler_name} + {config_desc}")

                    # Collect data
                    if handler_name == "oai-responses":
                        results = self.collect_openai_handler_data(handler, fleet_config)
                    elif handler_name == "pinecone-oai":
                        results = self.collect_pinecone_handler_data(
                            handler, fleet_config, fleet_config_name
                        )
                    elif handler_name == "oai-assistant":
                        results = self.collect_assistant_handler_data(handler, fleet_config)
                    else:
                        continue

                    # Save results
                    output_file = os.path.join(
                        output_dir,
                        f"{handler_name}_{config_desc}_{timestamp}.json"
                    )

                    with open(output_file, 'w') as f:
                        json.dump(results, f, indent=2)

                    output_files[f"{handler_name}_{config_desc}"] = output_file
                    self.logger.info(f"✓ Saved {len(results)} results to {output_file}")

            except Exception as e:
                self.logger.error(f"Error initializing or running {handler_name} handler: {e}")
                continue

        self.logger.info(f"\n{'='*60}")
        self.logger.info(f"Data collection complete. Files: {len(output_files)}")
        self.logger.info(f"{'='*60}\n")

        return output_files
