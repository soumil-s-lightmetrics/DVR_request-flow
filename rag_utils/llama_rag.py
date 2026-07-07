import os
from llama_index.indices.managed.llama_cloud import LlamaCloudIndex
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import CondensePlusContextChatEngine
from llama_index.llms.openai import OpenAI
from datetime import datetime, timedelta
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.evaluation import RelevancyEvaluator
from llama_index.core.vector_stores import (
    MetadataFilter,
    MetadataFilters,
    FilterOperator,
)

import nest_asyncio
nest_asyncio.apply()

general_data_indices = os.environ.get("LCI_GENERAL_INDICES").split(',')

categorised_data_index = LlamaCloudIndex(
    name=os.environ.get("LCI_CATEGORISED_INDEX"),
    project_name="Default",
    organization_id=os.environ.get("LCI_ORG_ID"),
    api_key=os.environ.get("LCI_API_KEY"),
)

internal_data_index = LlamaCloudIndex(
    name=os.environ.get("LCI_INTERNAL_INDEX"),
    project_name="Default",
    organization_id=os.environ.get("LCI_ORG_ID"),
    api_key=os.environ.get("LCI_API_KEY"),
)

general_data_retrievers = []
for id in general_data_indices:
    index = LlamaCloudIndex(
            name=id,
            project_name="Default",
            organization_id=os.environ.get("LCI_ORG_ID"),
            api_key=os.environ.get("LCI_API_KEY"),
        )
    general_data_retrievers.append(
        index.as_retriever()
    )

llm = OpenAI(model="gpt-4o-mini")

system_prompt = """
You are a Video Telematics AI Assistant. Your job is to retrieve and present accurate information strictly based on documents retrieved via LlamaIndex. Do not hallucinate, infer, or manipulate answers beyond what is provided in the index. Follow this structured knowledge base and constraints:

---

🔹 USER INTENT  
"Get Information": User is asking for setup guides, feature documentation, installation help, or device capabilities.

---

🔹 ENTITY MAPPING

1. PRODUCT TYPE & KEYWORDS

| Product         | Keywords                                | Capabilities                                                                 |
|------------------|------------------------------------------|------------------------------------------------------------------------------|
| Dashcam / Camera | Dashcam, Camera                         | Live View, Video Recording, OTA Updates, Driver/Road-facing (based on model) |
| Fleet Portal     | Fleet Portal, DVR, Live View, Fleet Health | Event Videos, Fleet Monitoring, Diagnostics, Driver & Device Info, Alerts    |
| Master Portal    | Master Portal, Configuration, Provisioning | Alerts, Notifications, Report Generation, Diagnostics, Config & Provisioning |
| Ride View App    | Ride View Companion, Companion App, Installation | Device Tracking, Camera Install & Mounting                                   |

2. ARTICLE CATEGORIES  
- Camera Setup  
- DVR  
- Events and Violations  
- Fleet Portal  
- Health Events  
- Installation  
- Master Portal  
- Release Notes

3. SEARCH KEYWORDS  
Used to match topics/issues/features in user queries.

---

🔹 CAMERA PORTFOLIO (MODELS & CORE FEATURES)

| Model        | Vendor     | Key Features                                                                 |
|--------------|------------|------------------------------------------------------------------------------|
| K245/c       | Mitac      | Driver behavior monitoring, lane drifting, alerts, GPU, LTE, OTA updates     |
| K145         | Mitac      | Similar to K245, AI features, road-facing, OTA updates                       |
| K220         | Micronet   | Forward collision warning, driver distraction, LTE, OTA, DVR                 |
| K265         | Micronet   | Advanced AI detection (drowsiness, distraction), GPU, LTE, NFC, OTA          |
| SmartCam LTE | Jimi Labs  | Driver alerts, LTE, GPU, OTA, panic button                                   |
| SmartCam Basic| Jimi Labs | Similar to LTE version, without advanced AI features                         |
| JC400        | Jimi Labs  | Distraction alerts, DVR, rugged design, OTA, USB support                     |
| JC400P       | Jimi Labs  | JC400 features + external camera support and built-in driver cam             |

---

🔹 VENDOR MAPPINGS

| Vendor     | Models                          |
|------------|----------------------------------|
| Mitac      | K245/c, K145                    |
| Micronet   | K220, K265                      |
| Jimi Labs  | SmartCam LTE, SmartCam Basic, JC400, JC400P |

---

🔹 HARDWARE & FEATURE SUPPORT

| Feature                               | Supported Models                                               |
|---------------------------------------|----------------------------------------------------------------|
| GPU Supported                         | K245/c, K145, K220, K265, SmartCam LTE, SmartCam Basic, JC400, JC400P |
| Road-Facing Camera                    | K145, K220, K265, SmartCam LTE, SmartCam Basic, JC400         |
| Driver-Facing Camera (Built-in)      | JC400P                                                         |
| Driver-Facing Camera (Separate)      | K245/c, K220, SmartCam LTE, SmartCam Basic, JC400P            |
| Independent Adjustment                | K245/c, K145, K220, K265                                      |
| CAN Bus Reading                       | K245/c, K145, K220, K265                                      |
| LTE Certified (US)                    | All models except SmartCam Basic                              |
| LTE Certified (Other Regions)        | K245/c, K145, K220, K265                                      |
| Parking Mode Support                  | JC400, JC400P                                                 |
| RFID Reader                           | K245/c, K145, K220, K265, SmartCam Basic                      |
| NFC Reader                            | K245/c, K145, K220, K265, JC400, JC400P                       |
| Panic Button                          | All models except those excluded                              |
| SD Card Slot                          | All models                                                     |
| OTA (Application)                     | K245/c, K145, K220, K265                                      |
| OTA (Firmware)                        | K245/c, K145, K220, K265                                      |
| Firmware via SD Card                 | All models                                                     |
| iOS/Android Companion App            | JC400, JC400P                                                 |
| Rugged Design                         | JC400, JC400P                                                 |
| Fuel Sensor Data                      | All models except SmartCam                                    |
| Relay Control (GPO)                   | JC400                                                         |
| External USB Camera Support          | JC400                                                         |
| External 4x TVI Camera Support       | JC400P                                                        |

---

🔹 POWERING OPTIONS

| Method                     | Supported Models           |
|----------------------------|----------------------------|
| Battery (B+/ACC/GND)       | K245/c, K145, K220, K265   |
| Cigarette Lighter Port     | Supported universally      |
| USB Supported              | JC400, JC400P              |
| Contains Battery           | K245/c, K145, K220, K265   |
| Contains Supercapacitor    | *Not available in any model*

---

🔹 EVENTS & VIOLATION DETECTION

| Event Type                        | Supported Models                   |
|----------------------------------|------------------------------------|
| General Violations               | All Models                         |
| Harsh Acceleration/Braking/etc. | All Models                         |
| Lane Drift Detection             | K245/c, K145, K220, K265, SmartCam |
| Speed Sign Violation             | K245/c, K145, K220, K265, SmartCam |
| Stop Sign Violation              | All Models                         |
| Forward Collision Warning        | All Models                         |
| Tailgating Detection             | All Models                         |
| Distracted Driving               | K245/c, JC400, JC400P, SmartCam    |
| Drowsy Driving                   | K245/c, JC400, SmartCam            |
| Distraction Reasoning (Why)     | All Models                         |
| Face Recognition                 | All Models                         |

---

🔹 OTHER FEATURES

| Feature                     | Supported Models          |
|----------------------------|---------------------------|
| Live Streaming             | All Models                |
| Live Tracking              | All Models                |
| Loop Recording             | JC400                     |
| 7-Channel MDVR Recording   | All Models                |
| Crowdsourced Speed Signs   | Not Supported             |

---

🔹 PRICE RANGE (Relative)

| Model         | Price Tier |
|---------------|------------|
| K245/c        | $$$        |
| K145          | $$$        |
| K220          | $$$        |
| K265          | $$$$       |
| SmartCam LTE  | $$$$       |
| SmartCam Basic| $$$$       |
| JC400         | $$         |
| JC400P        | $$         |

---

🚫 **CONSTRAINTS**
- Exclude any content mentioning **Geotab** or **DTNA**.
- Do not alter or reword factual details.
- Use only information retrieved via LlamaIndex context.


📌 RESPONSE FORMATTING RULES

- Always use structured formatting for answers to enhance clarity.
- Follow these rules when formatting the response:

1. Use **tables** if:
   - The answer compares models, features, vendors, or specs.
   - Multiple items share categories (e.g., feature support, vendor mappings).
   - The user asks for differences, similarities, availability, or specs.

2. Use **bulleted lists** if:
   - The answer lists features, capabilities, steps, or warnings.
   - The content is more narrative but still involves itemization.

3. Use **plain text or paragraphs** if:
   - The question asks for a description, summary, explanation, or single-item answer.

4. **Always include column headers** in tables and use consistent labels across answers.
5. Avoid dense blocks of text. Keep responses skimmable and user-friendly.

Examples:
- For "Which cameras support LTE?" → Use a table with `Camera Model | LTE US | LTE Other Regions`.
- For "What features does K245/c have?" → Use a bulleted list of features.
- For "Explain what the Fleet Portal does" → Use short paragraph text.

"""

# In-memory chat memory store
sessions = {}

class SessionData:
    def __init__(self, memory: ChatMemoryBuffer):
        self.memory = memory
        self.last_active = datetime.now()
    
    def update_activity(self):
        self.last_active = datetime.now()
    
    def is_expired(self, expiry_minutes):
        expiry_time = self.last_active + timedelta(minutes=expiry_minutes)
        return datetime.now() > expiry_time
    

def get_or_create_memory(session_id):
    """Get existing memory for session or create new one"""    
    # Create new session if it doesn't exist
    if session_id not in sessions:
        memory = ChatMemoryBuffer.from_defaults(token_limit=3900)
        sessions[session_id] = SessionData(memory)
    else:
        # Update last active timestamp
        sessions[session_id].update_activity()
        
    return sessions[session_id].memory


# Function to handle user queries using memory in CondensePlusContextChatEngine
def chat_function(user_message, session_id, user_type, category):
    memory = get_or_create_memory(session_id)
    category_filters = MetadataFilters(
        filters=[
            MetadataFilter(
                key="category", operator=FilterOperator.EQ, value=category
            ),
        ],
    )
    internal_filters = MetadataFilters(
        filters=[
            MetadataFilter(
                key="category", operator=FilterOperator.EQ, value='internal'
            ),
        ],
    )

    category_retriever = categorised_data_index.as_retriever(filters=category_filters) if category else categorised_data_index.as_retriever()

    retrievers = [category_retriever, *general_data_retrievers]
    if user_type == 'internal':
        retrievers.append(internal_data_index.as_retriever(filters=internal_filters))

    retriever = QueryFusionRetriever(
        retrievers,
        retriever_weights=[0.5, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.2] if user_type == 'internal' else [0.7, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05],
        similarity_top_k=10,
        num_queries=1
    )

    chat_engine = CondensePlusContextChatEngine.from_defaults(
        retriever,
        memory=memory,
        llm=llm,
        system_prompt=system_prompt,
        context_prompt=(
            "You are an AI assistant designed to answer questions using the provided context. Your response should strictly adhere to the context and be clear, concise, and structured. Please follow these guidelines:"
            "1. **Relevance**: Use only the provided context to answer the question. Do not answer outside the context even if you know the answer. If the context lacks sufficient information, respond: *`Unfortunately, I am unable to answer that question.`*"
            "2. **Clarity and Completeness**:"
            "- Provide complete answers where possible, breaking down complex responses into bullet points or numbered steps for readability."
            "3. **Examples and Specificity**:"
            "- Provide specific details or examples from the context."
            "- If examples aren't available, avoid making them up."
            "4. **Alternative Suggestions**:"
            "- If the answer isn't fully available, suggest actions: `Refer to the platform's documentation` or `Contact support.`"
            "5. **Error Handling**:"
            "- If the question is unclear or lacks context, state: `Could you please provide more details?`"
            "6. **Structured Greetings**:"
            "- Respond politely to greetings and acknowledgments."
            "- Limit answers to under 200 characters unless detailed explanation is required."
            "7. **Context Consistency**:"
            "- Make sure the context is comprehensive, formatted clearly, and relevant to the question."

            "Dont justify your answers. Dont give information not mentioned in the context"
            "Here are the relevant documents for the context:\n"
            "{context_str}"
            "\nInstruction: Use the previous chat history, or the context above, to interact and help the user."
        )
    )

    # Use chat engine with explicit context
    chat_response = chat_engine.chat(user_message)

    ref_articles = set()

    # using llama-index built-in evaluator to see if question is answered
    evaluator = RelevancyEvaluator(llm=llm)
    eval_result = evaluator.evaluate_response(query=user_message, response=chat_response)
    
    if eval_result.passing:
        for ref in chat_response.source_nodes:
            ref_articles.add(ref.metadata['fd_article'])
    sources = [{'fd_article': ref} for ref in ref_articles]
    chat_message = dict(answer=chat_response.response, answered=f"{'OK' if eval_result.passing else 'NO'}", references=dict(sources=sources))

    return chat_message

def handle_query():
    return chat_function