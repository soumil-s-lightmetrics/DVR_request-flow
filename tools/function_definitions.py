fetch_latest_release_notes = {
  "name": "fetch_latest_release_notes",
  "description": (
    "Handles all user queries related to the latest/newest version or release details—such as version numbers, recent updates, changes, highlights, summaries, changelog, feature additions, bug fixes, or release dates—for any supported platform. "
    "Supported platforms: Companion App (Mobile App, Driver App), Fleet Portal (Fleet Dashboard), Master Portal (TSP portal, TSP Dashboard), SDK (Device Application, APK, Camera Application, SDK and APK refer to software for the camera or dashcam). "
    "Handles implicit queries (e.g., 'what’s new?', 'any recent updates?'), prompts for clarification when ambiguous, and supports diverse question formats."
  ),
  "strict": True,
  "parameters": {
    "type": "object",
    "properties": {
      "platform_type": {
        "type": "string",
        "description": "The type of platform for which the release notes are requested. Supported platforms are Companion App (also recognised as: Mobile App, Driver App), Fleet Portal (also recognised as: Fleet Dashboard), Master Portal (also recognised as: TSP portal, TSP Dashboard), SDK (also recognised as: Device Application, APK, Camera Application)",
        "description": (
          "The type of platform for which the release notes are requested. "
          "Supported: Companion App (Mobile App, Driver App), Fleet Portal (Fleet Dashboard), Master Portal (TSP portal, TSP Dashboard), SDK (Device Application, APK, Camera Application)"
        ),
        "enum": [
          "companion_app",
          "fleet_portal",
          "master_portal",
          "sdk"
        ]
      }
    },
    "required": [
      "platform_type"
    ],
    "additionalProperties": False
  }
}