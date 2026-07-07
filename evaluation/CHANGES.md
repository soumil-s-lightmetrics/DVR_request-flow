# Evaluation Framework Updates

This document summarizes the updates made to support fleet-specific ground truth and single-handler evaluation.

## Changes Summary

### 1. Fleet-Specific Ground Truth Support ✅

**Problem**: Some questions have different correct answers depending on fleet configuration (e.g., "Can the system detect traffic light violations?" has different answers for Shield vs non-Shield fleets).

**Solution**: Extended ground truth format to support fleet-specific answers.

**Files Modified**:
- `evaluation/ground_truth.json` - Updated structure
- `evaluation/data_collector.py` - Added `_get_ground_truth_for_question()` method

**New Ground Truth Format**:

```json
{
  "Question with fleet-specific answer": {
    "no-fleet": {
      "ground_truth": "Generic answer...",
      "confidence": "high"
    },
    "shield": {
      "ground_truth": "Shield-specific answer...",
      "confidence": "high"
    },
    "non-shield": {
      "ground_truth": "Non-shield answer...",
      "confidence": "high"
    }
  },

  "Question with same answer for all configs": {
    "default": {
      "ground_truth": "Universal answer...",
      "confidence": "high"
    }
  }
}
```

**How It Works**:
1. During data collection, the framework identifies the fleet config for each query
2. When retrieving ground truth, it calls `_get_ground_truth_for_question(question, fleet_config_name)`
3. The method tries to find fleet-specific ground truth first, falls back to "default", then to old format
4. This ensures accurate evaluation where answers legitimately vary by fleet

**Examples Included**:
- "How do I provision the device?" - Different steps for Shield (Mitac cameras) vs non-Shield (Jimi cameras)
- "Can the system detect traffic light violations?" - Disabled for Shield, enabled for non-Shield

---

### 2. Single Handler Evaluation ✅

**Problem**: Users want to evaluate only one handler at a time (e.g., just Pinecone or just OpenAI) for faster iteration and cost savings.

**Solution**: Added `--handler` flag to CLI script.

**Files Modified**:
- `evaluation/evaluate_rag.py` - Added `--handler` argument and filtering logic

**New CLI Options**:

```bash
# Evaluate only OpenAI handler
python evaluation/evaluate_rag.py --all --handler openai

# Evaluate only Pinecone handler (all 3 configs)
python evaluation/evaluate_rag.py --all --handler pinecone

# Collect data for Pinecone only
python evaluation/evaluate_rag.py --collect --handler pinecone

# Evaluate existing Pinecone data only
python evaluation/evaluate_rag.py --evaluate --handler pinecone
```

**How It Works**:

**During Collection**:
- If `--handler` is specified, temporarily disable other handlers in config
- Only collect data for the specified handler

**During Evaluation**:
- If `--handler` is specified, filter collected data files by handler name
- Only evaluate files matching the specified handler
- Output directory includes handler name: `results/TIMESTAMP_pinecone/`

**Benefits**:
- Faster iteration during development
- Reduced API costs (only run one handler)
- Targeted testing of specific changes
- Easier debugging

---

### 3. Documentation Updates ✅

**Files Modified**:
- `README_EVALUATION.md` - Added sections on new features

**New Sections Added**:

1. **Key Features**
   - Fleet-Specific Ground Truth explanation and usage
   - Single Handler Evaluation explanation and examples

2. **Updated Quick Start**
   - New ground truth format examples
   - --handler flag usage examples

3. **Updated Examples**
   - Fleet-specific answer examples
   - Single handler evaluation workflows

---

## Usage Examples

### Example 1: Evaluate Question with Fleet-Specific Answer

**Question**: "Can the system detect traffic light violations?"

**Ground Truth** (`evaluation/ground_truth.json`):
```json
{
  "Can the system detect traffic light violations?": {
    "no-fleet": {
      "ground_truth": "Yes, the system can detect traffic light violations using the road-facing camera.",
      "confidence": "high"
    },
    "shield": {
      "ground_truth": "No, traffic light violation detection is disabled for Shield fleet configurations.",
      "confidence": "high"
    },
    "non-shield": {
      "ground_truth": "Yes, the system can detect traffic light violations and is fully enabled for non-Shield fleets.",
      "confidence": "high"
    }
  }
}
```

**Evaluation Results**:
- Pinecone with Shield config: Compared against Shield-specific ground truth
- Pinecone with non-Shield config: Compared against non-Shield ground truth
- Pinecone with no fleet: Compared against no-fleet ground truth
- OpenAI (no fleet): Compared against no-fleet ground truth

Each configuration is evaluated against the appropriate ground truth, ensuring fair and accurate metrics.

---

### Example 2: Evaluate Only Pinecone Handler

```bash
# Step 1: Collect data for Pinecone only (saves time and API costs)
python evaluation/evaluate_rag.py --collect --handler pinecone

# This collects:
# - pinecone_shield_TIMESTAMP.json
# - pinecone_non-shield_TIMESTAMP.json
# - pinecone_no-fleet_TIMESTAMP.json

# Step 2: Evaluate Pinecone data
python evaluation/evaluate_rag.py --evaluate --handler pinecone

# Results saved to: evaluation/results/TIMESTAMP_pinecone/
```

**Output**:
```
Evaluation Results:
| Handler  | Fleet Config | Faithfulness | Answer Relevancy | Context Precision | Context Recall |
|----------|--------------|--------------|------------------|-------------------|----------------|
| pinecone | shield       | 0.82         | 0.85             | 0.82              | 0.78           |
| pinecone | non-shield   | 0.83         | 0.86             | 0.83              | 0.79           |
| pinecone | no-fleet     | 0.84         | 0.84             | 0.80              | 0.81           |
```

---

## Migration Guide

If you have existing ground truth entries in the old format, they will continue to work (backward compatible). However, to leverage fleet-specific evaluation:

### Step 1: Identify Fleet-Sensitive Questions

Review your golden questions and identify which ones have different answers based on fleet config:
- Questions about disabled features (e.g., Traffic Light Violation for Shield)
- Questions about device-specific setup (Mitac vs Jimi cameras)
- Questions about version-specific features

### Step 2: Update Ground Truth Format

For each fleet-sensitive question, convert from:

**Old Format** (will still work, but not fleet-aware):
```json
{
  "Question": {
    "ground_truth": "Generic answer",
    "confidence": "high"
  }
}
```

**New Format** (fleet-aware):
```json
{
  "Question": {
    "no-fleet": {
      "ground_truth": "Generic answer",
      "confidence": "high"
    },
    "shield": {
      "ground_truth": "Shield-specific answer",
      "confidence": "high"
    },
    "non-shield": {
      "ground_truth": "Non-shield answer",
      "confidence": "high"
    }
  }
}
```

### Step 3: Re-run Evaluation

```bash
# Re-collect data (to ensure fresh responses)
python evaluation/evaluate_rag.py --collect

# Evaluate with new fleet-specific ground truth
python evaluation/evaluate_rag.py --evaluate
```

You should see improved metrics for fleet-specific questions where the handler provides the correct fleet-specific answer.

---

## Testing the Changes

### Test 1: Fleet-Specific Ground Truth

```bash
# 1. Add a test question with fleet-specific answers to ground_truth.json
# 2. Run collection
python evaluation/evaluate_rag.py --collect --handler pinecone

# 3. Check collected data files
cat evaluation/collected_data/pinecone_shield_*.json | jq '.[0]'
# Verify ground_truth field matches Shield-specific answer

cat evaluation/collected_data/pinecone_non-shield_*.json | jq '.[0]'
# Verify ground_truth field matches non-Shield answer
```

### Test 2: Single Handler Evaluation

```bash
# Evaluate only OpenAI
python evaluation/evaluate_rag.py --all --handler openai

# Check output directory
ls evaluation/results/
# Should see: TIMESTAMP_openai/

# Check results
cat evaluation/results/*/comparison_summary.csv
# Should only show OpenAI entries
```

---

## Impact on Metrics

With fleet-specific ground truth, you should see:

1. **Improved Context Recall** for fleet-specific questions
   - Handler correctly retrieves fleet-appropriate context
   - Ground truth now matches what the handler should return

2. **Improved Faithfulness** for fleet-specific answers
   - Handler's answer is faithful to retrieved fleet-specific context
   - No longer penalized for giving "different" but correct answer

3. **More Accurate Overall Metrics**
   - Fleet-specific questions no longer skew metrics negatively
   - True comparison of handler quality

---

## Backward Compatibility

All changes are backward compatible:

1. **Old ground truth format still works**
   - Entries with direct `ground_truth` field are supported
   - Falls back gracefully if fleet-specific entry not found

2. **Existing CLI commands work**
   - `--all`, `--collect`, `--evaluate` work as before
   - New `--handler` flag is optional

3. **Existing data files can be evaluated**
   - Old collected data can be re-evaluated with new ground truth
   - No need to re-collect unless you want fleet-specific ground truth

---

## Questions?

See `README_EVALUATION.md` for full documentation, or review:
- `evaluation/ground_truth.json` for examples
- `evaluation/data_collector.py:67-102` for implementation
- `evaluation/evaluate_rag.py:440-444` for --handler flag
