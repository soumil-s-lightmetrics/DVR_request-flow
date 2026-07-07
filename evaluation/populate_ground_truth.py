#!/usr/bin/env python3
"""
Script to populate ground_truth.json from existing response files.

Uses:
- data/oai-assistants-responses.json for no-fleet answers
- data/pinecone-gpt-4-shield-responses.json for shield answers
- data/pinecone-gpt-4-non-shield-responses.json for non-shield answers
"""

import json
from pathlib import Path

def load_json(file_path):
    """Load JSON file."""
    with open(file_path, 'r') as f:
        return json.load(f)

def save_json(file_path, data):
    """Save JSON file with pretty formatting."""
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def main():
    # Paths
    base_dir = Path(__file__).parent.parent

    oai_file = base_dir / "data" / "oai-assistants-responses.json"
    shield_file = base_dir / "data" / "pinecone-gpt-4-shield-responses.json"
    non_shield_file = base_dir / "data" / "pinecone-gpt-4-non-shield-responses.json"
    output_file = base_dir / "evaluation" / "ground_truth.json"

    # Load response files
    print(f"Loading {oai_file}...")
    oai_responses = load_json(oai_file)

    print(f"Loading {shield_file}...")
    shield_responses = load_json(shield_file)

    print(f"Loading {non_shield_file}...")
    non_shield_responses = load_json(non_shield_file)

    # Create lookup dictionaries
    oai_lookup = {item["question"]: item["answer"] for item in oai_responses}
    shield_lookup = {item["question"]: item["answer"] for item in shield_responses}
    non_shield_lookup = {item["question"]: item["answer"] for item in non_shield_responses}

    # Build ground truth
    ground_truth = {
        "_description": "Ground truth answers for golden questions. Supports fleet-specific answers.",
        "_instructions": "Format: { 'question': { 'no-fleet': {...}, 'shield': {...}, 'non-shield': {...} } }. If answer is same for all configs, use 'default' key.",
        "_note": "This file was auto-generated from existing response files. Review and edit as needed."
    }

    # Get all unique questions
    all_questions = set(oai_lookup.keys()) | set(shield_lookup.keys()) | set(non_shield_lookup.keys())

    print(f"\nProcessing {len(all_questions)} questions...")

    for question in sorted(all_questions):
        no_fleet_answer = oai_lookup.get(question, "")
        shield_answer = shield_lookup.get(question, "")
        non_shield_answer = non_shield_lookup.get(question, "")

        # Check if answers are significantly different (simple heuristic)
        # If all answers are similar, use default; otherwise use fleet-specific

        answers_are_different = False

        # Simple check: if any two answers differ by more than 10%, consider them different
        if shield_answer and non_shield_answer:
            # Compare shield vs non-shield
            if abs(len(shield_answer) - len(non_shield_answer)) > max(len(shield_answer), len(non_shield_answer)) * 0.1:
                answers_are_different = True
            elif shield_answer.lower().strip() != non_shield_answer.lower().strip():
                # Check for key phrases that indicate fleet-specific content
                shield_keywords = ["shield", "mitac", "gemini", "sprint", "traffic light", "disabled"]
                non_shield_keywords = ["non-shield", "jimi", "jc261", "jc261p", "enabled", "available"]

                shield_has_keywords = any(kw in shield_answer.lower() for kw in shield_keywords)
                non_shield_has_keywords = any(kw in non_shield_answer.lower() for kw in non_shield_keywords)

                if shield_has_keywords or non_shield_has_keywords:
                    answers_are_different = True

        # Build entry
        if answers_are_different and shield_answer and non_shield_answer:
            # Fleet-specific answers
            ground_truth[question] = {
                "no-fleet": {
                    "ground_truth": no_fleet_answer,
                    "confidence": "high"
                },
                "shield": {
                    "ground_truth": shield_answer,
                    "confidence": "high"
                },
                "non-shield": {
                    "ground_truth": non_shield_answer,
                    "confidence": "high"
                }
            }
            print(f"  ✓ {question[:60]}... [FLEET-SPECIFIC]")
        else:
            # Use default (same for all configs)
            # Prefer no-fleet answer if available, else shield, else non-shield
            default_answer = no_fleet_answer or shield_answer or non_shield_answer

            if default_answer:
                ground_truth[question] = {
                    "default": {
                        "ground_truth": default_answer,
                        "confidence": "high"
                    }
                }
                print(f"  ✓ {question[:60]}... [DEFAULT]")

    # Save ground truth
    print(f"\nSaving to {output_file}...")
    save_json(output_file, ground_truth)

    print(f"\n✓ Successfully populated ground_truth.json with {len(all_questions)} questions")
    print(f"  - Fleet-specific: {sum(1 for q, data in ground_truth.items() if not q.startswith('_') and 'shield' in data)}")
    print(f"  - Default: {sum(1 for q, data in ground_truth.items() if not q.startswith('_') and 'default' in data)}")

    print("\nNext steps:")
    print("1. Review evaluation/ground_truth.json")
    print("2. Manually verify fleet-specific questions are correctly categorized")
    print("3. Edit any answers that need refinement")

if __name__ == "__main__":
    main()
