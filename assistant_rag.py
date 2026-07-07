import json
import re
import requests
import os
import threading

from openai import OpenAI
from tools.ai_tools import AITools
from utils.s3_config_manager import S3ConfigManager
from threading import Lock
class AssistantUtil:
    """
    Streaming utility for simple question and answer with OpenAI Assistant API
    """
    
    def __init__(self, api_key, assistant_id, vecstore_id):
        self.client = OpenAI(api_key=api_key)
        self.api_key = api_key
        self.assistant_id = assistant_id
        self.vector_store_id = vecstore_id
        self.client_url = "https://api.openai.com/v1"
        self._thread_cache = {}

        bucket = os.getenv("S3_BUCKET_NAME")
        key = os.getenv("S3_CONFIG_KEY")
        self.config_manager = S3ConfigManager(bucket, key)
        self.config_manager.fetch_config()  # Initial fetch
        self.config_manager.start_periodic_refresh(interval_seconds=3600)  # Refresh every 1 hour
        
        self.ai_tools = AITools(self.config_manager)
    
    def _get_or_create_thread(self, session_id):
        """Get or create a thread ID for the given session"""
        if session_id not in self._thread_cache:
            thread = self.client.beta.threads.create()
            self._thread_cache[session_id] = thread.id
            
        return self._thread_cache[session_id]

    def _remove_citations(self, text):
        """Remove various citation patterns from text"""
        # Patterns for citation removal
        
        # 1. Remove antml:cite tags
        antml_pattern = r']*>(.*?)'
        cleaned_text = re.sub(antml_pattern, r'\1', text)
        
        # 2. Remove standard bracket citations [1], [2], etc.
        bracket_pattern = r'\[\d+\]'
        cleaned_text = re.sub(bracket_pattern, '', cleaned_text)
        
        # 3. Remove the specific format with Chinese brackets and file reference like 【4:1†First.pdf】
        chinese_bracket_pattern = r'【\d+:\d+†[^】]+】'
        cleaned_text = re.sub(chinese_bracket_pattern, '', cleaned_text)
        
        # 4. Remove parenthetical citations (Author, YYYY)
        parenthetical_pattern = r'\([A-Za-z]+,\s+\d{4}\)'
        cleaned_text = re.sub(parenthetical_pattern, '', cleaned_text)
        
        # Final trim
        cleaned_text = cleaned_text.strip()
        
        return cleaned_text
    
    def _get_cited_file(self, file_id):
        url = f"{self.client_url}/vector_stores/{self.vector_store_id}/files/{file_id}"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
        }
        try:
            response = requests.request("GET", url, headers=headers, data={})
            return response.json()
        except requests.exceptions.RequestException as e:  # This is the correct syntax
            print(f"Error getting citation file: {str(e)}")
    
    def stream_process_query(self, session_id, query, remove_citations=True, **thread_run_args):
        """
        Stream the processing of a query and yield status updates and results
        
        Args:
            session_id: Unique identifier for the user session
            query: User's query text
            
        Yields:
            Dict: Status updates and final result
        """
        # Get thread for this session
        thread_id = self._get_or_create_thread(session_id)
        
        try:
            # Initialize tool_outputs_references
            tool_outputs_references = []
            
            # Collected citations
            collected_citations = []
            collected_citation_ids = set()
            citation_threads = []
            citations_lock = Lock()
            
            # Yield initial status
            yield json.dumps({
                "event": "status",
                "data": {
                    "status": "starting"
                }
            }) + "\n"
            
            # Add user message to thread
            self.client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=query
            )
            
            # Yield status update
            yield json.dumps({
                "event": "status",
                "data": {
                    "status": "message_added"
                }
            }) + "\n"
            
            # Create a run with stream=True
            with self.client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=self.assistant_id,
                stream=True,
                **thread_run_args
            ) as stream:
                # Track the run ID
                run_id = None
                
                # Stream the run events
                for event in stream:
                    if event.event == "thread.run.created":
                        run_id = event.data.id
                        yield json.dumps({
                            "event": "status",
                            "data": {
                                "status": "run_created",
                                "run_id": run_id
                            }
                        }) + "\n"
                    
                    elif event.event == "thread.run.queued":
                        yield json.dumps({
                            "event": "status",
                            "data": {
                                "status": "queued"
                            }
                        }) + "\n"
                        
                    elif event.event == "thread.run.in_progress":
                        yield json.dumps({
                            "event": "status",
                            "data": {
                                "status": "in_progress"
                            }
                        }) + "\n"
                    
                    elif event.event == "thread.message.created":
                        yield json.dumps({
                            "event": "status",
                            "data": {
                                "status": "message_created",
                                "message_id": event.data.id
                            }
                        }) + "\n"
                    
                    elif event.event == "thread.message.delta":
                        # If there's a content delta, send it
                        if hasattr(event.data, 'delta') and hasattr(event.data.delta, 'content'):
                            for content_delta in event.data.delta.content:
                                if content_delta.type == 'text' and hasattr(content_delta, 'text') and hasattr(content_delta.text, 'value'):
                                    annotations = content_delta.text.annotations
                                    yield json.dumps({
                                        "event": "content_block",
                                        "data": {
                                            "text": self._remove_citations(content_delta.text.value) if annotations else content_delta.text.value,
                                        }
                                    }) + "\n"
                                    # Non-blocking citation collection
                                    if annotations:
                                        for annotation in annotations:
                                            if annotation.type == "file_citation":
                                                citation_id = annotation.file_citation.file_id
                                                if citation_id not in collected_citation_ids:
                                                    def fetch_and_store_citation(citation_id):
                                                        try:
                                                            cited_file = self._get_cited_file(citation_id)
                                                            metadata = cited_file.get('attributes', {})
                                                            with citations_lock:
                                                                collected_citations.append(metadata)
                                                                collected_citation_ids.add(citation_id)
                                                        except Exception as e:
                                                            print(f"Error processing citation: {str(e)}")
                                                    t = threading.Thread(target=fetch_and_store_citation, args=(citation_id,))
                                                    t.start()
                                                    citation_threads.append(t)
                    
                    elif event.event == "thread.run.requires_action":
                        # Indicates that the run requires a tool call to be completed
                        yield json.dumps({
                            "event": "status",
                            "data": {
                                "status": "requires_action",
                                "message": "Processing required tool calls"
                            }
                        }) + "\n"
                        
                        # Handle the required actions
                        required_action = event.data.required_action
                        tool_outputs = []
                        
                        # Process each tool call
                        for tool_call in required_action.submit_tool_outputs.tool_calls:
                            tool_call_id = tool_call.id
                            function_name = tool_call.function.name
                            function_args = json.loads(tool_call.function.arguments)
                            
                            try:
                                result = self.ai_tools.call_tool_function(function_name, function_args)
                            except Exception as e:
                                print(f"Error calling tool function {function_name}: {str(e)}")
                                result = {}

                            # Add to tool outputs
                            tool_outputs.append({
                                "tool_call_id": tool_call_id,
                                "output": json.dumps(result.get('data', '')),
                            })

                            tool_outputs_references.extend(result.get('references', []))
                                
                            # Notify the client about the tool call
                            yield json.dumps({
                                "event": "tool_call",
                                "data": {
                                    "function": function_name,
                                    "arguments": function_args
                                }
                            }) + "\n"
                        
                        # Submit all tool outputs
                        if tool_outputs:
                            # Submit the tool outputs to continue the run
                            self.client.beta.threads.runs.submit_tool_outputs_and_poll(
                                thread_id=thread_id,
                                run_id=run_id,
                                tool_outputs=tool_outputs
                            )
                            
                            yield json.dumps({
                                "event": "status",
                                "data": {
                                    "status": "tool_outputs_submitted",
                                    "message": "continueing result generation"
                                }
                            }) + "\n"
                        
                    elif event.event == "thread.run.completed":
                        yield json.dumps({
                            "event": "status",
                            "data": {
                                "status": "completed"
                            }
                        }) + "\n"
                    
                    elif event.event == "thread.run.failed":
                        error_message = "Run failed"
                        if hasattr(event.data, 'last_error'):
                            error_message = event.data.last_error.message
                            
                        yield json.dumps({
                            "event": "error",
                            "data": {
                                "status": "failed",
                                "error": error_message
                            }
                        }) + "\n"
                        return
                
                # After streaming, if we need to get the full message...
                try:
                    # Wait for all citation threads to finish
                    for t in citation_threads:
                        t.join()
                    
                    # Add tool outputs references to citations
                    for ref in tool_outputs_references:
                        collected_citations.append(ref)
                    
                    # filter out duplicates
                    collected_citations = list({v['id']: v for v in collected_citations}.values())
                        
                    if collected_citations and len(collected_citations) > 0:
                        yield json.dumps({
                            "event": "content_block_references",
                            "data": {
                                "references": collected_citations
                            }
                        }) + "\n"

                    messages = self.client.beta.threads.messages.list(
                        thread_id=thread_id,
                        order="desc",
                        limit=1
                    )
                    
                    if messages.data:
                        latest_msg = messages.data[0]
                        
                        # Get full text
                        full_text = ""
                        if messages.data:
                            latest_msg = messages.data[0]
                        
                        for content_item in latest_msg.content:
                            if content_item.type == 'text':
                                cleaned_text = self._remove_citations(content_item.text.value) if remove_citations else content_item.text.value
                                full_text += cleaned_text
                        
                        # Send the complete response as a final message
                        yield json.dumps({
                            "event": "complete",
                            "data": {
                                "message_id": latest_msg.id,
                                "thread_id": thread_id,
                                "run_id": run_id,
                                "full_text": full_text,
                                "references": collected_citations
                            }
                        }) + "\n"
                except Exception as e:
                    print(f"Error getting full message: {str(e)}")
                
        except Exception as e:
            # Yield the error
            yield json.dumps({
                "event": "error",
                "data": {
                    "status": "error",
                    "error": str(e)
                }
            }) + "\n"
    
    def reset_session(self, session_id):
        """Reset a session by creating a new thread"""
        try:
            # Create new thread
            thread = self.client.beta.threads.create()
            
            # Update cache
            old_thread_id = self._thread_cache.get(session_id)
            self._thread_cache[session_id] = thread.id
            
            return {
                "status": "success",
                "new_thread_id": thread.id,
                "old_thread_id": old_thread_id
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }