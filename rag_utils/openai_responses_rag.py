import openai
import json
import os
import threading
import requests
import logging
from typing import Any, Optional, List, Dict, Callable
from dataclasses import dataclass
from threading import Lock

from utils.s3_config_manager import S3ConfigManager
from utils.prompts import lisa_main_system_prompt
from tools.ai_tools import AITools
from tools.function_definitions import fetch_latest_release_notes

class BreakEventLoop(Exception):
    """Custom exception to break the event loop after recursion."""
    pass

@dataclass
class ToolResult:
    """Container for tool execution results."""
    tool_call_id: str
    result: Any
    error: Optional[str] = None

class OpenAIResponsesHandler:
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

    def __init__(self, api_key: Optional[str], vecstore_id: str, model: str, config_manager: S3ConfigManager) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.client = openai.OpenAI(api_key=self.api_key)
        self.model = model
        self.vector_store_id = vecstore_id
        self.config_manager = config_manager
        self.client_url = "https://api.openai.com/v1"
        self.custom_tools: Dict[str, dict] = {}

        self.ai_tools = AITools(self.config_manager)
        self.logger = logging.getLogger(__name__)
        if not self.logger.hasHandlers():
            logging.basicConfig(level=logging.INFO)

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
        tools = [
            {
                "type": "file_search",
                "vector_store_ids": [self.vector_store_id],
                "max_num_results": 3,
            }
        ]
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
        stream = self.client.responses.create(
            model=self.model,
            tools=tools_config,
            tool_choice=context_args.get("tool_choice", "auto"),
            input=messages,
            instructions=instructions,
            previous_response_id=prev_resp_id,
            stream=True
        )

        response_id = None
        response = None
        final_content = ""
        tool_outputs_references = context_args.get("tool_outputs_references", [])
        collected_citations = []
        collected_citation_ids = set()
        citation_threads = []
        citations_lock = Lock()

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
                            final_content += content.text

        def handle_output_text_delta(event):
            yield json.dumps({
                "event": "output_text_delta",
                "data": {"delta": event.delta}
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
                yield from self.stream_response(messages, instructions, None, response_id, **context)
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

        try:
            for event in stream:
                response_id_from_event = getattr(getattr(event, "response", None), "id", response_id)
                self.logger.info(f"Event: {event.type}, Seq: {getattr(event, 'sequence_number', None)}, Response_id: {response_id_from_event}, Prev_resp_id: {prev_resp_id}")
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
        for ref in tool_outputs_references:
            collected_citations.append(ref)
        collected_citations = list({v['id']: v for v in collected_citations if isinstance(v, dict) and 'id' in v}.values())
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

    def process_question(
        self,
        question: str,
        prev_resp_id: Optional[str],
        instructions: str = lisa_main_system_prompt,
        **context_args: Any
    ):
        """
        Process a user question using the Responses API with tool support.
        Yields streamed response events.
        """
        self.setup_default_tools()
        messages = [{"role": "user", "content": question}]
        tools_config = self.create_tools_config()
        yield from self.stream_response(messages, instructions, tools_config, prev_resp_id, **context_args)
