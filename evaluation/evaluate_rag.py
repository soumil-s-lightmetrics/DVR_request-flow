#!/usr/bin/env python3
"""
RAG Evaluation Script using Ragas

Evaluates RAG handlers using ragas metrics and generates comparison reports.

Usage:
    python evaluate_rag.py --collect  # Collect data first
    python evaluate_rag.py --evaluate  # Evaluate collected data
    python evaluate_rag.py --all  # Do both
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv
load_dotenv()

import pandas as pd
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
    context_recall
)
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from evaluation.config import load_config, EvaluationConfig
from evaluation.data_collector import RAGDataCollector
from utils.s3_config_manager import S3ConfigManager


class RAGEvaluator:
    """
    Evaluates RAG handlers using ragas metrics.
    """

    def __init__(self, config: EvaluationConfig, logger: Optional[logging.Logger] = None):
        """
        Initialize evaluator.

        Args:
            config: Evaluation configuration
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

        # Initialize ragas metrics
        self.metrics = {
            "faithfulness": faithfulness,
            "answer_relevancy": answer_relevancy,
            "context_precision": context_precision,
            "context_recall": context_recall
        }

        # Filter to configured metrics
        self.active_metrics = [
            self.metrics[m] for m in self.config.metrics
            if m in self.metrics
        ]

        if not self.active_metrics:
            self.logger.warning("No valid metrics configured, using all metrics")
            self.active_metrics = list(self.metrics.values())

        # Initialize LLM and embeddings for ragas
        self.llm = ChatOpenAI(
            model=self.config.ragas.llm_model,
            temperature=self.config.ragas.llm_temperature
        )
        self.embeddings = OpenAIEmbeddings(
            model=self.config.ragas.embeddings_model
        )

    def load_evaluation_data(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Load collected evaluation data from JSON file.

        Args:
            file_path: Path to collected data JSON file

        Returns:
            List of evaluation data points
        """
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)
            self.logger.info(f"Loaded {len(data)} data points from {file_path}")
            return data
        except FileNotFoundError:
            self.logger.error(f"Data file not found: {file_path}")
            return []
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing data JSON: {e}")
            return []

    def format_for_ragas(self, data: List[Dict[str, Any]]) -> Dataset:
        """
        Convert collected data to ragas-compatible Dataset format.

        Args:
            data: List of evaluation data points

        Returns:
            HuggingFace Dataset formatted for ragas
        """
        # Filter out error entries
        valid_data = [d for d in data if "error" not in d.get("metadata", {})]

        if len(valid_data) < len(data):
            self.logger.warning(f"Filtered out {len(data) - len(valid_data)} error entries")

        # Convert to ragas format
        ragas_data = {
            'question': [],
            'answer': [],
            'contexts': [],  # List of lists
            'ground_truth': []
        }

        for item in valid_data:
            ragas_data['question'].append(item['question'])
            ragas_data['answer'].append(item['answer'])

            # Ensure contexts is a list
            contexts = item.get('contexts', [])
            if not isinstance(contexts, list):
                contexts = [contexts] if contexts else []

            # Ragas expects List[List[str]]
            ragas_data['contexts'].append(contexts)

            ragas_data['ground_truth'].append(item.get('ground_truth', ''))

        # Create HuggingFace Dataset
        dataset = Dataset.from_dict(ragas_data)
        self.logger.info(f"Created ragas dataset with {len(dataset)} samples")

        return dataset

    def run_evaluation(
        self,
        dataset: Dataset,
        handler_name: str,
        fleet_config: Optional[str]
    ) -> Dict[str, Any]:
        """
        Run ragas evaluation on dataset.

        Args:
            dataset: HuggingFace Dataset formatted for ragas
            handler_name: Name of handler being evaluated
            fleet_config: Fleet config name (or None)

        Returns:
            Evaluation results dictionary
        """
        config_desc = fleet_config or "no-fleet"
        self.logger.info(f"\nEvaluating {handler_name} ({config_desc})...")

        try:
            # Run ragas evaluation
            result = evaluate(
                dataset,
                metrics=self.active_metrics,
                llm=self.llm,
                embeddings=self.embeddings
            )

            # Convert to dict
            results_dict = {
                "handler": handler_name,
                "fleet_config": config_desc,
                "num_samples": len(dataset),
                "metrics": {}
            }

            # Extract metric scores
            # Note: ragas EvaluationResult has buggy __contains__, so we access directly
            for metric_name in self.config.metrics:
                try:
                    metric_value = result[metric_name]
                    # Handle both single values and lists (ragas may return per-sample scores)
                    if isinstance(metric_value, (list, tuple)):
                        # Take mean of per-sample scores
                        import numpy as np
                        results_dict["metrics"][metric_name] = float(np.mean(metric_value))
                    else:
                        results_dict["metrics"][metric_name] = float(metric_value)
                except (KeyError, AttributeError) as e:
                    self.logger.warning(f"Metric {metric_name} not found in results: {e}")

            self.logger.info(f"✓ Evaluation complete for {handler_name} ({config_desc})")
            self.logger.info(f"  Metrics: {results_dict['metrics']}")

            return results_dict

        except Exception as e:
            import traceback
            self.logger.error(f"✗ Error during evaluation: {e}")
            self.logger.error(f"Traceback:\n{traceback.format_exc()}")
            return {
                "handler": handler_name,
                "fleet_config": config_desc,
                "error": str(e)
            }

    def evaluate_all_collected_data(
        self,
        collected_data_dir: Optional[str] = None,
        handler_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Evaluate all collected data files.

        Args:
            collected_data_dir: Directory containing collected data files
            handler_filter: Only evaluate this handler (e.g., 'openai', 'pinecone')

        Returns:
            Dictionary of all evaluation results
        """
        collected_data_dir = collected_data_dir or self.config.collected_data_dir

        # Find all JSON files in collected data directory
        data_files = list(Path(collected_data_dir).glob("*.json"))

        if not data_files:
            self.logger.error(f"No data files found in {collected_data_dir}")
            return {}

        self.logger.info(f"Found {len(data_files)} data files to evaluate")

        all_results = {}

        for data_file in data_files:
            # Extract handler and config from filename
            # Format: {handler}_{config}_{timestamp}.json
            filename = data_file.stem
            parts = filename.split('_')

            if len(parts) >= 2:
                handler_name = parts[0]
                fleet_config = parts[1] if parts[1] != "no-fleet" else None
            else:
                self.logger.warning(f"Unexpected filename format: {filename}")
                continue

            # Skip if handler filter is specified and doesn't match
            if handler_filter and handler_name != handler_filter:
                self.logger.debug(f"Skipping {filename} (not matching filter: {handler_filter})")
                continue

            # Load data
            data = self.load_evaluation_data(str(data_file))

            if not data:
                continue

            # Format for ragas
            dataset = self.format_for_ragas(data)

            # Run evaluation
            result = self.run_evaluation(dataset, handler_name, fleet_config)

            # Store result
            key = f"{handler_name}_{fleet_config or 'no-fleet'}"
            all_results[key] = result

        return all_results

    def generate_reports(self, results: Dict[str, Any], output_dir: str):
        """
        Generate evaluation reports in multiple formats.

        Args:
            results: Evaluation results dictionary
            output_dir: Output directory for reports
        """
        os.makedirs(output_dir, exist_ok=True)

        # 1. Save full results as JSON
        json_file = os.path.join(output_dir, "eval_results.json")
        with open(json_file, 'w') as f:
            json.dump(results, f, indent=2)
        self.logger.info(f"✓ Saved full results to {json_file}")

        # 2. Generate comparison summary CSV
        summary_data = []
        for key, result in results.items():
            if "error" in result:
                continue

            row = {
                "handler": result["handler"],
                "fleet_config": result["fleet_config"],
                "num_samples": result["num_samples"]
            }
            row.update(result["metrics"])
            summary_data.append(row)

        if summary_data:
            df = pd.DataFrame(summary_data)
            csv_file = os.path.join(output_dir, "comparison_summary.csv")
            df.to_csv(csv_file, index=False)
            self.logger.info(f"✓ Saved comparison summary to {csv_file}")

            # Print summary table
            print("\n" + "="*80)
            print("EVALUATION RESULTS SUMMARY")
            print("="*80 + "\n")
            print(df.to_string(index=False))
            print("\n" + "="*80 + "\n")

        # 3. Generate HTML report
        self._generate_html_report(results, output_dir)

    def _generate_html_report(self, results: Dict[str, Any], output_dir: str):
        """Generate interactive HTML dashboard."""
        html_file = os.path.join(output_dir, "evaluation_dashboard.html")

        # Build HTML
        html = """
<!DOCTYPE html>
<html>
<head>
    <title>RAG Evaluation Dashboard</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        h1 {
            color: #333;
            border-bottom: 3px solid #4CAF50;
            padding-bottom: 10px;
        }
        h2 {
            color: #555;
            margin-top: 30px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        th {
            background-color: #4CAF50;
            color: white;
        }
        tr:hover {
            background-color: #f5f5f5;
        }
        .metric-value {
            font-weight: bold;
            color: #2196F3;
        }
        .timestamp {
            color: #888;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>RAG Handler Evaluation Dashboard</h1>
        <p class="timestamp">Generated: """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + """</p>

        <h2>Evaluation Results</h2>
        <table>
            <tr>
                <th>Handler</th>
                <th>Fleet Config</th>
                <th>Samples</th>
"""

        # Add metric columns
        if results:
            first_result = next(iter(results.values()))
            if "metrics" in first_result:
                for metric_name in first_result["metrics"].keys():
                    html += f"                <th>{metric_name.replace('_', ' ').title()}</th>\n"

        html += "            </tr>\n"

        # Add data rows
        for key, result in results.items():
            if "error" in result:
                continue

            html += "            <tr>\n"
            html += f"                <td>{result['handler']}</td>\n"
            html += f"                <td>{result['fleet_config']}</td>\n"
            html += f"                <td>{result['num_samples']}</td>\n"

            for metric_value in result["metrics"].values():
                html += f"                <td class='metric-value'>{metric_value:.3f}</td>\n"

            html += "            </tr>\n"

        html += """
        </table>
    </div>
</body>
</html>
"""

        with open(html_file, 'w') as f:
            f.write(html)

        self.logger.info(f"✓ Saved HTML dashboard to {html_file}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Evaluate RAG handlers using ragas")
    parser.add_argument(
        "--config",
        type=str,
        default="evaluation/evaluation_config.yaml",
        help="Path to evaluation config file"
    )
    parser.add_argument(
        "--collect",
        action="store_true",
        help="Collect data from handlers"
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Evaluate collected data"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Collect and evaluate"
    )
    parser.add_argument(
        "--handler",
        type=str,
        choices=["oai-responses", "pinecone-oai", "oai-assistant"],
        help="Evaluate only specific handler (default: all enabled handlers)"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output directory for results"
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)

    # Load configuration
    try:
        config = load_config(args.config)
        logger.info(f"Loaded configuration from {args.config}")
    except Exception as e:
        logger.error(f"Error loading configuration: {e}")
        return 1

    # Determine what to do
    do_collect = args.collect or args.all
    do_evaluate = args.evaluate or args.all

    if not (do_collect or do_evaluate):
        parser.print_help()
        return 1

    # Data collection
    if do_collect:
        logger.info("\n" + "="*60)
        logger.info("STARTING DATA COLLECTION")
        logger.info("="*60 + "\n")

        try:
            # Filter config for specific handler if requested
            if args.handler:
                logger.info(f"Filtering for handler: {args.handler}")
                # Temporarily disable other handlers
                for handler_name in config.handlers:
                    if handler_name != args.handler:
                        config.handlers[handler_name].enabled = False

            # Initialize S3 config manager
            bucket = os.environ.get("S3_BUCKET_NAME")
            key = os.environ.get("S3_CONFIG_KEY")
            config_manager = S3ConfigManager(bucket, key)
            config_manager.fetch_config()  # Initial fetch

            collector = RAGDataCollector(config, config_manager, logger)
            output_files = collector.run_collection()

            logger.info(f"\nData collection complete. Generated {len(output_files)} files.")

        except Exception as e:
            logger.error(f"Error during data collection: {e}")
            return 1

    # Evaluation
    if do_evaluate:
        logger.info("\n" + "="*60)
        logger.info("STARTING EVALUATION")
        logger.info("="*60 + "\n")

        try:
            evaluator = RAGEvaluator(config, logger)

            # If handler is specified, filter collected data files
            if args.handler:
                logger.info(f"Evaluating only {args.handler} handler data")
                # evaluator.evaluate_all_collected_data will only process files matching the handler

            results = evaluator.evaluate_all_collected_data(handler_filter=args.handler)

            # Generate output directory
            if args.output:
                output_dir = args.output
            else:
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                handler_suffix = f"_{args.handler}" if args.handler else ""
                output_dir = os.path.join(config.results_dir, f"{timestamp}{handler_suffix}")

            # Generate reports
            evaluator.generate_reports(results, output_dir)

            logger.info(f"\n✓ Evaluation complete. Results saved to {output_dir}")

        except Exception as e:
            logger.error(f"Error during evaluation: {e}")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
