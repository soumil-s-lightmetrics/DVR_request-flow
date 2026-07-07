import os
import requests
import base64
from utils.s3_config_manager import S3ConfigManager
from logger import debug_logger
class AITools:
    def __init__(self, config_manager: S3ConfigManager):
        self.freshdesk_api_key = os.getenv("FRESHDESK_API_KEY")
        self.freshdesk_api_base_url = os.getenv("FRESHDESK_API_BASE_URL")
        self.debug_logger = debug_logger()
        self.config_manager = config_manager
        self.current_config = self.config_manager.get_config()
        self._cache = {}

    def _get_solution_article_content(self, id):
        """
        Fetch the content of a solution article from Freshdesk with in-memory caching.

        Args:
            id (str): The ID of the solution article to fetch.
        Returns:
            dict: The content of the solution article.
        """
        if id in self._cache:
            debug_logger().debug(f"Cache hit for solution article ID: {id}")
            return self._cache[id]

        if not self.freshdesk_api_key or not self.freshdesk_api_base_url:
            raise ValueError("Freshdesk API key or base URL is not set in environment variables.")

        url = f"{self.freshdesk_api_base_url}/api/v2/solutions/articles/{id}"
        auth_string = f"{self.freshdesk_api_key}:X"
        encoded_auth = base64.b64encode(auth_string.encode()).decode()
        headers = {'Authorization': f'Basic {encoded_auth}'}

        try:
            debug_logger().debug(f"Fetching solution article content for ID: {id}")
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            article_content = response.json()
            self._cache[id] = article_content  # Store in cache
            return article_content
        except requests.exceptions.RequestException as e:
            raise ValueError(f"Error fetching solution article content: {e}")

    def _fetch_latest_release_notes(self, args):
        """
        Handle the fetch_latest_release_notes tool call by fetching from an API.

        Args:
            args (dict): Arguments containing the platform type.
        Returns:
            dict: The latest release notes content and references for the specified platform.
        Raises:
            ValueError: If the platform type is not provided or if the API call fails.
        """
        platform_type = args.get("platform_type")
        if not platform_type:
            raise ValueError("platform_type is required")

        latest_release_notes_details = self.current_config.get("latest_release_notes", {}).get(platform_type)
        if not latest_release_notes_details:
            raise ValueError(f"Latest release notes details for {platform_type} not found in config.")
        try:
            # Fetch release notes content and references
            release_notes = []
            references = []
            for detail in latest_release_notes_details:
                fd_article_url = detail.get('fd_article_url', '')
                article_id = fd_article_url.split("/")[-1].strip()
                article_content = self._get_solution_article_content(article_id)
                release_notes.append(article_content.get('description', ''))
                references.append({"fd_article_url": fd_article_url, "id": article_id})

            return {
                "data": "\n".join(filter(None, release_notes)),
                "references": references
            }
        except Exception as e:
            raise ValueError(f"Error in fetch_latest_release_notes: {str(e)}")
        
    def call_tool_function(self, name, args):
        """
        Call the appropriate tool function based on the name provided.
        """
        tool_functions = {
            "fetch_latest_release_notes": self._fetch_latest_release_notes
        }

        if name in tool_functions:
            return tool_functions[name](args)
        raise ValueError(f"Tool {name} not found.")