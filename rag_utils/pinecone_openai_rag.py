import openai
import json
import os
import threading
import requests
import logging
from typing import Any, Optional, List, Dict, Callable
from dataclasses import dataclass
from threading import Lock
from pinecone import Pinecone

from utils.s3_config_manager import S3ConfigManager
from utils.prompts import lisa_main_system_prompt
from tools.ai_tools import AITools
from tools.function_definitions import fetch_latest_release_notes
from utils.fleet_config_manager import FleetConfigManager
from pydantic import BaseModel
from partial_json_parser import loads, Allow
from logger import debug_logger

class BreakEventLoop(Exception):
    """Custom exception to break the event loop after recursion."""
    pass

@dataclass
class LisaResponse(BaseModel):
    answer: str
    sources: list[str]

@dataclass
class ToolResult:
    """Container for tool execution results."""
    tool_call_id: str
    result: Any
    error: Optional[str] = None

class PineconeOpenAIResponsesHandler:
    """
    Handler for OpenAI Responses API with tool support.
    Provides methods for registering tools, handling tool calls, streaming responses, and processing questions.
    """

    # Event type constants
    EVENT_RESPONSE_CREATED = "response.created"
    EVENT_RESPONSE_COMPLETED = "response.completed"
    EVENT_OUTPUT_TEXT_DELTA = "response.output_text.delta"
    EVENT_OUTPUT_TEXT_ANNOTATION_ADDED = "response.output_text.annotation.added"
    EVENT_OUTPUT_ITEM_DONE = "response.output_item.done"
    EVENT_RESPONSE_FAILED = "response.failed"
    EVENT_ERROR = "error"

    def __init__(self, api_key: Optional[str], vecstore_id: str, model: str, config_manager: S3ConfigManager, fleet_config_manager: FleetConfigManager) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.client = openai.OpenAI(api_key=self.api_key)
        self.model = model
        self.vector_store_id = vecstore_id
        self.config_manager = config_manager
        self.client_url = "https://api.openai.com/v1"
        self.custom_tools: Dict[str, dict] = {}
        self.fleet_config_manager = fleet_config_manager
        self.json_stream_parser = loads

        self.pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
        self.lm_kb_dense_index = self.pc.Index(host=os.getenv("PINECONE_INDEX_HOST"))

        self.ai_tools = AITools(self.config_manager)
        self.logger = debug_logger()

    def _get_cited_file(self, file_id: str) -> dict:
        """Fetch metadata for a cited file from the vector store."""
        url = f"{self.client_url}/vector_stores/{self.vector_store_id}/files/{file_id}"
        headers = {'Authorization': f'Bearer {self.api_key}'}
        try:
            response = requests.get(url, headers=headers)
            return response.json()
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error getting citation file: {str(e)}")
            return {}
    
    def _get_file_search_filters(self, fleet_config: Optional[dict]) -> Optional[dict]:
        """Generate file search filters based on fleet configuration."""
        if not fleet_config:
            return None

        # Call get_filter_attributes ONCE instead of 9 times (9x performance improvement)
        filter_attrs = self.fleet_config_manager.get_filter_attributes(fleet_config)

        # Extract all needed attributes from the result
        fleet_portal_version_major = filter_attrs["fleet_portal_version_major"]
        fleet_portal_version_minor = filter_attrs["fleet_portal_version_minor"]
        fleet_portal_version_patch = filter_attrs["fleet_portal_version_patch"]
        device_apk_version_major = filter_attrs["device_apk_version_major"]
        device_apk_version_minor = filter_attrs["device_apk_version_minor"]
        device_models_in = filter_attrs["device_models_in"]
        plans_in = filter_attrs["plans_in"]
        event_type_in = filter_attrs["event_type_in"]
        required_features = filter_attrs["required_features"]

        filters = {
            "$and": [
                {
                    "$or": [
                        { "fleet_portal_version_major": { "$lt": fleet_portal_version_major } },
                        {
                            "$and": [
                                { "fleet_portal_version_major": { "$eq": fleet_portal_version_major } },
                                { "fleet_portal_version_minor": { "$lt": fleet_portal_version_minor } }
                            ]
                        },
                        {
                            "$and": [
                                { "fleet_portal_version_major": { "$eq": fleet_portal_version_major } },
                                { "fleet_portal_version_minor": { "$eq": fleet_portal_version_minor } },
                                { "fleet_portal_version_patch": { "$lte": fleet_portal_version_patch } }
                            ]
                        },
                        {
                            "$and": [
                                { "fleet_portal_version_major": { "$eq": 0 } },
                                { "fleet_portal_version_minor": { "$eq": 0 } },
                                { "fleet_portal_version_patch": { "$eq": 0 } }
                            ]
                        }
                    ]
                },
                {
                    "$or": [
                        { "device_apk_version_major": { "$lt": device_apk_version_major } },
                        {
                            "$and": [
                                { "device_apk_version_major": { "$eq": device_apk_version_major } },
                                { "device_apk_version_minor": { "$lt": device_apk_version_minor } }
                            ]
                        },
                        {
                            "$and": [
                                { "device_apk_version_major": { "$eq": device_apk_version_major } },
                                { "device_apk_version_minor": { "$eq": device_apk_version_minor } },
                            ]
                        },
                        {
                            "$and": [
                                { "device_apk_version_major": { "$eq": 0 } },
                                { "device_apk_version_minor": { "$eq": 0 } },
                            ]
                        }
                    ]
                },
                {
                    "$or": [
                        {
                            "device_models_in": { "$in": device_models_in }
                        },
                        {
                            "device_models_in": { "$exists": False }
                        }
                    ]
                },
                {
                    "$or": [
                        { "plans_in": { "$in": plans_in } },
                        { "plans_in": { "$exists": False } }
                    ]
                },
                {
                    "$or": [
                        { "plans_nin": { "$nin": plans_in } },
                        { "plans_nin": { "$exists": False } }
                    ]
                },
                {
                    "$or": [
                        {
                            "event_type_in": { "$nin": event_type_in }
                        },
                        {
                            "event_type_in": { "$exists": False }
                        }
                    ]
                },
                {
                    "$or": [
                        {
                            "required_features": { "$nin": required_features }
                        },
                        {
                            "required_features": { "$exists": False }
                        }
                    ]
                },
                {
                    "fd_category": {"$ne": "Master Portal"}
                },
            ]
        }
        return filters if filters else None

    def register_custom_tool(self, name: str, description: str, strict: bool, params: dict) -> None:
        """Register a custom tool function."""
        self.custom_tools[name] = {
            "schema": {
                "type": "function",
                "name": name,
                "description": description,
                "strict": strict,
                "parameters": params
            }
        }

    def setup_default_tools(self) -> None:
        """Register default tools, including fetch_latest_release_notes."""
        self.register_custom_tool(
            name=fetch_latest_release_notes["name"],
            description=fetch_latest_release_notes["description"],
            strict=fetch_latest_release_notes["strict"],
            params=fetch_latest_release_notes["parameters"]
        )

    def create_tools_config(self) -> List[dict]:
        """Create tools configuration for the API call."""
        # tools = [
        #     {
        #         "type": "file_search",
        #         "vector_store_ids": [self.vector_store_id],
        #         "max_num_results": 3,
        #         "filters": self._get_file_search_filters(fleet_config)
        #     }
        # ]
        tools = []
        tools.extend(tool["schema"] for tool in self.custom_tools.values())
        return tools

    def handle_tool_calls(self, tool_calls: List[Any]) -> List[ToolResult]:
        """Handle tool calls and return results as ToolResult objects."""
        results: List[ToolResult] = []
        for tool_call in tool_calls:
            tool_call_obj = tool_call.dict()
            tool_call_id = tool_call_obj.get("call_id")
            tool_type = tool_call_obj.get("type", "")
            try:
                if tool_type == "function_call":
                    function_name = tool_call_obj.get("name")
                    function_args = json.loads(tool_call_obj.get("arguments", None))
                    if function_name in self.custom_tools:
                        self.logger.info(f"Executing custom tool: {function_name}")
                        result = self.ai_tools.call_tool_function(function_name, function_args)
                        results.append(ToolResult(tool_call_id=tool_call_id, result=result))
                    else:
                        results.append(ToolResult(tool_call_id=tool_call_id, result=None, error=f"Unknown function: {function_name}"))
            except Exception as e:
                self.logger.error(f"Error executing tool {tool_call_id}: {str(e)}")
                results.append(ToolResult(tool_call_id=tool_call_id, result=None, error=str(e)))
        return results

    def create_tool_messages(self, tool_results: List[ToolResult]) -> List[dict]:
        """Create tool result messages to send back to the API."""
        messages = []
        for result in tool_results:
            if result.error:
                content = f"Error: {result.error}"
            else:
                content = json.dumps(result.result) if isinstance(result.result, dict) else str(result.result)
            messages.append({
                "type": "function_call_output",
                "call_id": result.tool_call_id,
                "output": str(content)
            })
        return messages

    def stream_response(
        self,
        messages: List[dict],
        instructions: str,
        tools_config: Optional[List[dict]] = None,
        prev_resp_id: Optional[str] = None,
        **context_args
    ):
        """
        Stream the response from the OpenAI API, handling events, tool calls, and citations.
        Yields JSON lines for each event.
        """
        response_id = None
        response = None
        final_content = ""
        tool_outputs_references = context_args.get("tool_outputs_references", [])
        collected_citations = []
        collected_citation_ids = set()
        citation_threads = []
        citations_lock = Lock()
        last_answer_text_len = 0
        sources = set()

        def handle_response_created(event):
            nonlocal response_id
            response_id = event.response.id

        def handle_response_completed(event):
            nonlocal response, final_content
            response = event.response
            for output in response.output:
                if output.type == "message" and output.role == "assistant":
                    for content in output.content:
                        if content.type == "output_text":
                            final_content += content.parsed.answer or ""
                            sources.update(content.parsed.sources or [])
 
        def handle_output_text_delta(event):
            nonlocal last_answer_text_len
            partial_obj = self.json_stream_parser(event.snapshot, Allow.STR | Allow.OBJ)
            answer = partial_obj.get("answer", "")
            delta = answer[last_answer_text_len:]
            last_answer_text_len = len(answer)
            if delta:
                yield json.dumps({
                    "event": "output_text_delta",
                    "data": {"delta": delta}
                }) + "\n"

        def handle_output_text_annotation_added(event):
            annotation = event.annotation
            if annotation and annotation['type'] == "file_citation":
                citation_id = annotation['file_id']
                if citation_id not in collected_citation_ids:
                    def fetch_and_store_citation(cid):
                        try:
                            cited_file = self._get_cited_file(cid)
                            metadata = cited_file.get('attributes', {})
                            with citations_lock:
                                collected_citations.append(metadata)
                                collected_citation_ids.add(cid)
                        except Exception as e:
                            self.logger.error(f"Error processing citation: {str(e)}")
                    t = threading.Thread(target=fetch_and_store_citation, args=(citation_id,))
                    t.start()
                    citation_threads.append(t)

        def handle_output_item_done(event):
            item = event.item
            if item.type == "function_call":
                tool_results = self.handle_tool_calls([item])
                tool_messages = self.create_tool_messages(tool_results)
                messages.extend(tool_messages)
                for result in tool_results:
                    if result.result:
                        tool_outputs_references.extend(result.result.get("references", []))
                context = {"tool_outputs_references": tool_outputs_references}
                yield from self.stream_response(messages, instructions, tools_config, response_id, **context)
                raise BreakEventLoop()
            return False

        def handle_response_failed(event):
            yield json.dumps({
                "event": "error",
                "data": {
                    "response_id": getattr(response, 'id', None),
                    "error": event.error.message
                }
            }) + "\n"

        def handle_error(event):
            yield json.dumps({
                "event": "error",
                "data": {"response_id": response_id}
            }) + "\n"

        event_handlers: Dict[str, Callable] = {
            self.EVENT_RESPONSE_CREATED: handle_response_created,
            self.EVENT_RESPONSE_COMPLETED: handle_response_completed,
            self.EVENT_OUTPUT_TEXT_DELTA: handle_output_text_delta,
            self.EVENT_OUTPUT_TEXT_ANNOTATION_ADDED: handle_output_text_annotation_added,
            self.EVENT_OUTPUT_ITEM_DONE: handle_output_item_done,
            self.EVENT_RESPONSE_FAILED: handle_response_failed,
            self.EVENT_ERROR: handle_error,
        }

        with self.client.responses.stream(
            model=self.model,
            tools=tools_config,
            tool_choice=context_args.get("tool_choice", "auto"),
            input=messages,
            instructions=instructions,
            previous_response_id=prev_resp_id,
            text_format=LisaResponse,
        ) as stream:
            try:
                for event in stream:
                    response_id_from_event = getattr(getattr(event, "response", None), "id", response_id)
                    # self.logger.info(f"Event: {event.type}, Seq: {getattr(event, 'sequence_number', None)}, Response_id: {response_id_from_event}, Prev_resp_id: {prev_resp_id}")
                    handler = event_handlers.get(event.type)
                    if handler:
                        result = handler(event)
                        # If handler yields, yield its output
                        if hasattr(result, '__iter__') and not isinstance(result, bool):
                            for x in result:
                                yield x
            except BreakEventLoop:
                self.logger.info(f"Breaking out of event loop due to function call recursion. Response_id: {response_id}, Prev_resp_id: {prev_resp_id}")
                return

        for t in citation_threads:
            t.join()
        # Add tool output references and sources to collected_citations
        for ref in tool_outputs_references:
            collected_citations.append(ref)
        for source in sources:
            id = source.split("/")[-1]
            citation = {"id": id, "fd_article_url": source}
            collected_citations.append(citation)

        # Deduplicate collected_citations by 'id'
        unique_citations = {}
        for citation in collected_citations:
            if isinstance(citation, dict) and 'id' in citation:
                unique_citations[citation['id']] = citation
        collected_citations = list(unique_citations.values())
        if collected_citations:
            yield json.dumps({
                "event": "content_block_references",
                "data": {"references": collected_citations}
            }) + "\n"

        yield json.dumps({
           "event": "complete",
           "data": {
               "response_id": response_id,
               "full_text": final_content,
               "references": collected_citations,
               "usage": getattr(response, 'usage', None) and response.usage.total_tokens
           }
        }) + "\n"

    def rewrite_user_query(self, user_query, last_resp_id):
        """
        Pulls history from OpenAI servers to turn follow-ups into standalone queries.
        """
        if not last_resp_id:
            return user_query

        # 1. FETCH HISTORY FROM OPENAI
        # This retrieves all previous user/assistant turns linked to the last response
        history_items = self.client.responses.input_items.list(last_resp_id, order="asc")
        
        # 2. FORMAT HISTORY FOR REWRITER
        # input_items contain 'role' and 'content' blocks
        formatted_history = []
        for item in history_items.data:
            role = item.role
            # Extract text from content list (usually first element for text messages)
            content = item.content[0].text if hasattr(item.content[0], 'text') else ""
            formatted_history.append(f"{role.upper()}: {content}")

        history_str = "\n".join(formatted_history)

        # 3. REWRITE PROMPT
        prompt = f"""
        Given the history below, rephrase the follow-up question to be a standalone search query.

        HISTORY:
        {history_str}
        
        FOLLOW-UP: {user_query}
        STANDALONE QUERY:
        """

        res = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        return res.choices[0].message.content.strip()

    def get_context(self, query: str, fleet_config: Optional[dict] = None) -> tuple[str, List[str]]:
        """Retrieve context from the vector store based on the user query and fleet configuration."""
        search_filter = self._get_file_search_filters(fleet_config)

        # Generate embedding for the query
        embedding_response = self.client.embeddings.create(
            model="text-embedding-3-small",
            input=query,
        )
        query_vector = embedding_response.data[0].embedding

        results = self.lm_kb_dense_index.query(
            namespace="__default__",
            vector=query_vector,
            top_k=25,
            include_metadata=True,
            filter=search_filter
        )

        context_chunks = []
        sources = set()
        documents_to_rerank = []

        for match in results.matches:
            text = match.metadata.get('chunk_text', '')
            article_id = match.metadata.get('fd_article_id')

            documents_to_rerank.append({
                "id": match.id,
                "text": text,
                "fd_article_id": int(article_id) if article_id is not None else None
            })
        
        rerank_response = self.pc.inference.rerank(
            model="bge-reranker-v2-m3",
            query=query,
            documents=[doc['text'] for doc in documents_to_rerank],
            top_n=5,
            return_documents=False # We already have them
        )

        for result in rerank_response.data:
            # result.index tells us which document from our original list was chosen
            doc = documents_to_rerank[result.index]
            score = result.score # This is a refined relevance score
            
            if score < 0.1:
                continue  # Skip low relevance scores
            
            
            self.logger.debug(f"Reranked Doc Index: {result.index}, Article ID: {doc['fd_article_id']},  Score: {result.score:.4f}")

            url = f"https://lightmetrics.freshdesk.com/a/solutions/articles/{doc['fd_article_id']}"
            sources.add(url)
            context_chunks.append(f"[Source: {url} | Relevance: {score:.2f}]\n{doc['text']}")

        return "\n\n".join(context_chunks), list(sources)

    def process_question(
        self,
        question: str,
        prev_resp_id: Optional[str],
        instructions: str = lisa_main_system_prompt,
        fleet_config: Optional[dict] = None,
        **context_args: Any
    ):
        """
        Process a user question using the Responses API with tool support.
        Yields streamed response events.
        """
        self.setup_default_tools()
        search_query = self.rewrite_user_query(question, prev_resp_id)
        self.logger.debug(f"Rewritten search query: {search_query}")

        context_text, context_sources = self.get_context(search_query, fleet_config=fleet_config)
        self.logger.debug(f"Retrieved context text length: {len(context_text)}")
        self.logger.debug(f"Retrieved context sources: {context_sources}")

        instructions += f"\n\nUse the following context to answer the question:\n{context_text}\n\n"
        messages = [{"role": "user", "content": search_query}]
        tools_config = self.create_tools_config()
        yield from self.stream_response(messages, instructions, tools_config, prev_resp_id, **context_args)
