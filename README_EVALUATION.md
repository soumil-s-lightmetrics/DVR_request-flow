# RAG Evaluation Framework

A comprehensive evaluation workflow using the **ragas** library to compare RAG handler implementations (OpenAIResponsesHandler vs PineconeOpenAIResponsesHandler).

## Overview

This framework evaluates RAG systems using core quality metrics:
- **Faithfulness**: How grounded answers are in retrieved context
- **Answer Relevancy**: How relevant answers are to questions
- **Context Precision**: Precision of retrieved context
- **Context Recall**: Recall of retrieved context

### Evaluation Strategy

- **OpenAIResponsesHandler**: Evaluated without fleet config (generic use case)
- **PineconeOpenAIResponsesHandler**: Evaluated with 3 configurations:
  - Shield fleet config
  - Non-shield fleet config
  - No fleet config

This allows comparison of:
1. Baseline performance (both handlers without fleet config)
2. Impact of fleet-specific metadata filtering on Pinecone
3. Overall quality vs performance tradeoffs

## Quick Start

### 1. Installation

```bash
# Install evaluation dependencies
pip install -r requirements-eval.txt

# Verify ragas installation
python -c "import ragas; print(f'ragas {ragas.__version__}')"
```

### 2. Configure Environment

Ensure these environment variables are set:

```bash
export OPENAI_API_KEY="your-key"
export VECTOR_STORE_ID="your-vector-store-id"
export PINECONE_API_KEY="your-key"
export PINECONE_INDEX_HOST="your-host"
export PINECONE_REMOTE_API_KEY="your-remote-key"
```

### 3. Prepare Ground Truth (First Time Only)

Edit `evaluation/ground_truth.json` to add reference answers for golden questions. The framework supports **fleet-specific ground truth** for questions that have different correct answers based on fleet configuration.

**Format 1: Fleet-Specific Answers** (when answer varies by fleet config)

```json
{
  "How do I provision the device?": {
    "no-fleet": {
      "ground_truth": "To provision a device, access the master Portal...",
      "confidence": "high"
    },
    "shield": {
      "ground_truth": "For Shield fleet, access Fleet Portal and configure for Mitac cameras. Note: Traffic Light Violation is disabled...",
      "confidence": "high"
    },
    "non-shield": {
      "ground_truth": "For non-Shield fleet, access Fleet Portal and configure for Jimi cameras. All events are available...",
      "confidence": "high"
    }
  }
}
```

**Format 2: Same Answer for All Configs** (when answer doesn't vary)

```json
{
  "What is EDVR?": {
    "default": {
      "ground_truth": "EDVR (Event-Driven Video Recording) is a feature...",
      "confidence": "high"
    }
  }
}
```

The evaluation framework will automatically select the correct ground truth based on the fleet configuration being evaluated.

### 4. Run Evaluation

**Option A: CLI Script (Automated)**

```bash
# Collect data and evaluate (all-in-one)
python evaluation/evaluate_rag.py --all

# Or run steps separately:
python evaluation/evaluate_rag.py --collect   # Collect data
python evaluation/evaluate_rag.py --evaluate  # Evaluate collected data

# Evaluate only one handler (useful for targeted testing)
python evaluation/evaluate_rag.py --all --handler openai      # Only OpenAI
python evaluation/evaluate_rag.py --all --handler pinecone    # Only Pinecone

# Collect data for one handler, then evaluate it
python evaluation/evaluate_rag.py --collect --handler pinecone
python evaluation/evaluate_rag.py --evaluate --handler pinecone

# Specify custom config
python evaluation/evaluate_rag.py --all --config path/to/config.yaml

# Specify custom output directory
python evaluation/evaluate_rag.py --all --output results/my_evaluation
```

**Option B: Jupyter Notebook (Interactive)**

```bash
# Launch Jupyter
jupyter notebook evaluation/evaluate_rag.ipynb
```

Then follow the notebook cells to:
- Collect data (or load pre-collected)
- Run ragas evaluation
- Analyze results with visualizations
- Generate insights

## File Structure

```
evaluation/
├── __init__.py                    # Package initialization
├── config.py                      # Configuration management classes
├── evaluation_config.yaml         # Default configuration
├── ground_truth.json              # Reference answers for evaluation
├── data_collector.py              # Data collection from handlers
├── evaluate_rag.py                # CLI evaluation script
├── evaluate_rag.ipynb             # Interactive Jupyter notebook
│
├── collected_data/                # Inference outputs (generated)
│   ├── openai_no-fleet_TIMESTAMP.json
│   ├── pinecone_shield_TIMESTAMP.json
│   ├── pinecone_non-shield_TIMESTAMP.json
│   └── pinecone_no-fleet_TIMESTAMP.json
│
└── results/                       # Evaluation results (generated)
    └── TIMESTAMP/
        ├── eval_results.json
        ├── comparison_summary.csv
        └── evaluation_dashboard.html
```

## Configuration

### Editing `evaluation_config.yaml`

The configuration file controls all aspects of evaluation:

```yaml
# Which handlers to evaluate
handlers:
  openai:
    enabled: true
    fleet_configs: [null]  # No fleet config
  pinecone:
    enabled: true
    fleet_configs: [shield, non-shield, null]

# Fleet configurations
fleet_configs:
  shield:
    fleet_portal_version: "v9.20.0"
    device_apk_version: "v1.20.4"
    camera_models: ["mitac-gemini", "mitac-sprint-k220"]
    disabled_standard_events: ["Traffic-Light-Violated"]
    plan: "SHIELD"

# Metrics to evaluate
metrics:
  - faithfulness
  - answer_relevancy
  - context_precision
  - context_recall

# Ragas settings
ragas:
  llm:
    model: "gpt-4o-mini"  # LLM for evaluation
    temperature: 0
  embeddings:
    model: "text-embedding-3-small"
```

### Customizing Fleet Configurations

To test with different fleet configs:

1. Add new config to `fleet_configs` section
2. Reference it in handler's `fleet_configs` list
3. Run evaluation

Example:

```yaml
fleet_configs:
  my-custom-fleet:
    fleet_portal_version: "v11.0.0"
    device_apk_version: "v2.0.0"
    camera_models: ["my-camera"]
    disabled_standard_events: []
    plan: "CUSTOM"

handlers:
  pinecone:
    fleet_configs: [shield, non-shield, my-custom-fleet, null]
```

## Understanding Results

### Metrics Explanation

1. **Faithfulness** (0-1, higher is better)
   - Measures if answers are grounded in retrieved context
   - High score = answer doesn't hallucinate beyond context
   - Important for: Factual accuracy, trust

2. **Answer Relevancy** (0-1, higher is better)
   - Measures if answer is relevant to the question
   - High score = answer directly addresses question
   - Important for: User satisfaction

3. **Context Precision** (0-1, higher is better)
   - Measures precision of retrieved context chunks
   - High score = retrieved chunks are relevant
   - Important for: Retrieval quality

4. **Context Recall** (0-1, higher is better)
   - Measures if all necessary context was retrieved
   - High score = all needed information was found
   - Important for: Completeness

### Output Files

**1. `eval_results.json`**
Full evaluation results including:
- Per-configuration metrics
- Metadata (handler, fleet config, sample count)
- Timestamps

**2. `comparison_summary.csv`**
Tabular comparison:

| Handler | Fleet Config | Faithfulness | Answer Relevancy | Context Precision | Context Recall |
|---------|--------------|--------------|------------------|-------------------|----------------|
| OpenAI | no-fleet | 0.85 | 0.82 | 0.78 | 0.80 |
| Pinecone | shield | 0.82 | 0.85 | 0.82 | 0.78 |
| ... | ... | ... | ... | ... | ... |

**3. `evaluation_dashboard.html`**
Interactive HTML dashboard with formatted results table.

### Interpreting Results

**High faithfulness, low relevancy**
- Handler retrieves good context but doesn't answer question well
- Action: Improve prompting or answer generation

**Low precision, high recall**
- Handler retrieves too much irrelevant context
- Action: Improve retrieval filtering or reranking

**High precision, low recall**
- Handler is too restrictive in retrieval
- Action: Expand retrieval scope or adjust thresholds

**Comparing handlers:**
- OpenAI: Typically higher faithfulness (uses native file_search)
- Pinecone: May have better relevancy (uses reranking)
- Fleet configs: Should show targeted retrieval for config-specific questions

## Key Features

### Fleet-Specific Ground Truth

One of the key features of this evaluation framework is support for **fleet-specific ground truth**. This is critical for accurately evaluating RAG systems that provide different answers based on fleet configuration.

**Why is this important?**

Some questions have legitimately different correct answers depending on the fleet configuration:

- **"Can the system detect traffic light violations?"**
  - Shield fleet: "No, this feature is disabled for Shield fleets"
  - Non-Shield fleet: "Yes, this feature is fully enabled"

Without fleet-specific ground truth, the evaluation would incorrectly penalize the handler for giving the "wrong" answer when it's actually correct for that fleet configuration.

**How to use it:**

1. Identify questions where the answer varies by fleet config
2. In `ground_truth.json`, provide separate answers for each config:
   - `no-fleet`: Generic answer (for handlers without fleet config)
   - `shield`: Answer specific to Shield fleets
   - `non-shield`: Answer specific to non-Shield fleets
3. For questions with same answer regardless of config, use `default` key

The framework automatically matches ground truth to fleet config during evaluation.

### Single Handler Evaluation

Use the `--handler` flag to evaluate only one handler at a time. This is useful when:

- Testing changes to a specific handler
- Debugging issues with one implementation
- Saving API costs by not running full comparison
- Iterating quickly during development

**Examples:**

```bash
# Evaluate only Pinecone handler (all 3 configs: shield, non-shield, no-fleet)
python evaluation/evaluate_rag.py --all --handler pinecone

# Evaluate only OpenAI handler (no-fleet config only)
python evaluation/evaluate_rag.py --all --handler openai

# Collect data for Pinecone only, evaluate later
python evaluation/evaluate_rag.py --collect --handler pinecone
# ... make changes to evaluation logic ...
python evaluation/evaluate_rag.py --evaluate --handler pinecone
```

The output directory will include the handler name: `results/2026-01-02_11-30-00_pinecone/`

## Advanced Usage

### Adding New Metrics

Ragas supports additional metrics. To add:

1. Import metric in `evaluate_rag.py`:
```python
from ragas.metrics import answer_similarity
```

2. Add to config:
```yaml
metrics:
  - faithfulness
  - answer_relevancy
  - answer_similarity  # NEW
```

3. Ensure ground truth includes necessary fields

### Evaluating Custom Questions

To evaluate specific questions instead of golden questions:

```python
from evaluation.data_collector import RAGDataCollector

collector = RAGDataCollector(config, config_manager)

custom_questions = [
    "What is the latest firmware version?",
    "How do I update the camera?"
]

# Collect data for custom questions
results = collector.collect_openai_handler_data(
    handler,
    fleet_config=None,
    questions=custom_questions
)
```

### Evaluating New Handlers

To add a new handler:

1. Update `evaluation_config.yaml`:
```yaml
handlers:
  my_new_handler:
    enabled: true
    class_name: "MyNewHandler"
    module: "rag_utils.my_new_rag"
    fleet_configs: [null]
```

2. Update `data_collector.py` to add collection logic:
```python
def collect_my_new_handler_data(self, handler, fleet_config, questions):
    # Implement collection logic
    pass
```

3. Update `run_collection()` to initialize new handler

### Batch Evaluation

To evaluate multiple times for statistical significance:

```bash
# Run 5 evaluations
for i in {1..5}; do
    python evaluation/evaluate_rag.py --all --output results/run_$i
done

# Aggregate results
python -c "
import pandas as pd
from pathlib import Path

runs = []
for run_dir in Path('evaluation/results').glob('run_*'):
    df = pd.read_csv(run_dir / 'comparison_summary.csv')
    df['run'] = run_dir.name
    runs.append(df)

combined = pd.concat(runs)
summary = combined.groupby(['handler', 'fleet_config']).agg(['mean', 'std'])
print(summary)
"
```

## Troubleshooting

### Issue: "No data files found"

**Solution**: Run data collection first:
```bash
python evaluation/evaluate_rag.py --collect
```

### Issue: "Missing ground truth"

**Solution**: Add ground truth entries to `evaluation/ground_truth.json` for all golden questions.

### Issue: Ragas evaluation fails with API errors

**Possible causes:**
- OpenAI API rate limits
- Invalid API keys
- Network issues

**Solutions:**
- Check environment variables
- Reduce evaluation dataset size
- Add retry logic or delays

### Issue: Context extraction returns empty lists

**Cause**: Handler streaming format may have changed

**Solution**:
- Check `data_collector.py` stream parsing logic
- Compare with handler implementation
- Add debug logging to see raw stream events

### Issue: Metrics are all 0 or NaN

**Possible causes:**
- Empty ground truth
- Malformed dataset
- Ragas LLM failures

**Solutions:**
- Verify ground truth is populated
- Check dataset format (contexts must be List[List[str]])
- Test ragas with smaller sample

## Best Practices

1. **Ground Truth Quality**
   - Spend time on high-quality reference answers
   - Mark confidence levels
   - Include 10-15 critical questions minimum

2. **Incremental Evaluation**
   - Start with small sample (5-10 questions) to validate setup
   - Gradually increase to full 40 questions
   - Run periodic evaluations during development

3. **Version Control**
   - Commit evaluation configs and ground truth
   - Tag evaluation runs with git commits
   - Document any changes to evaluation methodology

4. **Comparative Analysis**
   - Always compare against baseline
   - Track metrics over time
   - Look for statistically significant differences

5. **Cost Management**
   - Ragas uses OpenAI API for evaluation
   - ~10-20 API calls per question for all metrics
   - For 40 questions × 4 configs = 3200-6400 API calls
   - Use gpt-4o-mini to minimize cost

## Cost Estimation

**Per Evaluation Run:**
- Data Collection: ~160 API calls (40 questions × 4 configs)
- Ragas Evaluation: ~6400 API calls (40 questions × 4 configs × 4 metrics × 10 calls/metric)

**Using gpt-4o-mini:**
- Data collection: ~$0.50
- Ragas evaluation: ~$5.00
- **Total: ~$5.50 per full evaluation**

**Cost Reduction Tips:**
- Use smaller question sample for development
- Cache collected data, only re-run evaluation
- Use gpt-4o-mini instead of gpt-4o for ragas

## Citation

This evaluation framework uses:

- **Ragas**: [https://github.com/explodinggradients/ragas](https://github.com/explodinggradients/ragas)
- **HuggingFace Datasets**: [https://huggingface.co/docs/datasets](https://huggingface.co/docs/datasets)
- **LangChain**: [https://python.langchain.com](https://python.langchain.com)

## Support

For issues or questions:
1. Check this README first
2. Review the plan file: `/Users/manju/.claude/plans/whimsical-bubbling-comet.md`
3. Examine example outputs in `evaluation/results/`
4. Review ragas documentation: [https://docs.ragas.io](https://docs.ragas.io)
