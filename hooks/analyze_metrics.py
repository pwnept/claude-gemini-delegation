#!/usr/bin/env python3
"""
Analyze delegation metrics to identify optimization opportunities

Usage:
    python analyze_metrics.py [--days N]
    
Options:
    --days N    Analyze metrics from the last N days (default: 7)
""" 

import sys
import csv
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter
from typing import List, Optional, Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def parse_csv_row(row: list) -> Optional[Tuple[str, str, int, int]]:
    """Parse a single CSV line into components."""
    if len(row) != 4:
        return None
    
    timestamp, task, lines, tokens = row
    try:
        return timestamp, task, int(lines), int(tokens)
    except ValueError:
        return None


def parse_csv_line(line: str) -> Optional[Tuple[str, str, int, int]]:
    """Parse a legacy CSV line string for callers importing the old helper."""
    return parse_csv_row(next(csv.reader([line])))


def load_metrics(metrics_dir: Path, days: int) -> List[Tuple[str, str, int, int]]:
    """Load metrics from the last N days."""
    metrics = []
    
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        log_file = metrics_dir / f"delegation-{date}.csv"
        
        if not log_file.exists():
            continue
        
        with log_file.open('r', encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)

            for row in reader:
                parsed = parse_csv_row(row)
                if parsed:
                    metrics.append(parsed)
    
    return metrics


def analyze_metrics(metrics: List[Tuple[str, str, int, int]]):
    """Analyze and display metrics."""
    if not metrics:
        print("📊 No delegation metrics found")
        print("Make sure you're running delegations with the post-delegation hook")
        return
    
    # Calculate aggregates
    total_delegations = len(metrics)
    total_lines = sum(m[2] for m in metrics)
    total_tokens = sum(m[3] for m in metrics)
    avg_lines = total_lines / total_delegations
    avg_tokens = total_tokens / total_delegations
    
    # Find tasks that consistently exceed limits
    excessive_tasks = Counter()
    efficient_tasks = Counter()
    
    for _, task, _, tokens in metrics:
        if tokens > 250:
            excessive_tasks[task] += 1
        elif tokens < 100:
            efficient_tasks[task] += 1
    
    # Display results
    print("📊 Delegation Metrics Analysis")
    print("=" * 50)
    print(f"\n✅ Summary:")
    print(f"   Total delegations: {total_delegations}")
    print(f"   Average response length: {avg_lines:.1f} lines")
    print(f"   Average token usage: {avg_tokens:.0f} tokens")
    
    # Token efficiency assessment
    if avg_tokens < 150:
        print(f"   🎉 Excellent! Your prompts are highly optimized")
    elif avg_tokens < 200:
        print(f"   👍 Good compression. Room for minor improvements")
    else:
        print(f"   ⚠️  Average tokens above target. Review prompts below")
    
    # Show problematic tasks
    if excessive_tasks:
        print(f"\n⚠️  Tasks Needing Prompt Refinement:")
        print("   (Consistently >250 tokens)")
        for task, count in excessive_tasks.most_common(5):
            print(f"   • {task}: {count} occurrences")
    
    # Show efficient tasks
    if efficient_tasks:
        print(f"\n✅ Most Efficient Tasks:")
        print("   (<100 tokens per response)")
        for task, count in efficient_tasks.most_common(5):
            print(f"   • {task}: {count} occurrences")
    
    # Calculate token savings estimate
    # Assume without compression, average would be 1500 tokens
    baseline_tokens = 1500 * total_delegations
    actual_tokens = total_tokens
    savings = baseline_tokens - actual_tokens
    savings_pct = (savings / baseline_tokens) * 100
    
    print(f"\n💰 Estimated Token Savings:")
    print(f"   Baseline (no compression): ~{baseline_tokens:,} tokens")
    print(f"   Actual usage: ~{actual_tokens:,} tokens")
    print(f"   Savings: ~{savings:,} tokens ({savings_pct:.0f}%)")
    
    # Recommendations
    print(f"\n💡 Recommendations:")
    if avg_tokens > 200:
        print("   • Review prompts for tasks listed above")
        print("   • Add more aggressive compression directives")
        print("   • Consider using max_lines parameter more strictly")
    else:
        print("   • Current delegation strategy is working well")
        print("   • Keep monitoring weekly to maintain efficiency")
    
    # Daily breakdown
    print(f"\n📅 Daily Breakdown:")
    daily_counts = Counter()
    daily_tokens = {}
    
    for timestamp, task, lines, tokens in metrics:
        date = timestamp.split()[0]
        daily_counts[date] += 1
        daily_tokens[date] = daily_tokens.get(date, 0) + tokens
    
    for date in sorted(daily_counts.keys(), reverse=True)[:7]:
        count = daily_counts[date]
        avg_tok = daily_tokens[date] / count
        print(f"   {date}: {count:3d} delegations, avg {avg_tok:.0f} tokens")


def main():
    """Main execution."""
    days = 7
    
    # Parse command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] in ('-h', '--help'):
            print(__doc__)
            sys.exit(0)
        if sys.argv[1] == '--days' and len(sys.argv) > 2:
            days = int(sys.argv[2])
    
    # Prefer .gemini-delegation/ for metrics; fall back to .claude/.
    current_dir = Path.cwd()
    agent_dir = None
    for directory in (current_dir, *current_dir.parents):
        for name in (".gemini-delegation", ".claude"):
            candidate = directory / name
            if candidate.exists():
                agent_dir = candidate
                break
        if agent_dir:
            break
    if agent_dir is None:
        print("❌ Error: neither .gemini-delegation nor .claude directory found")
        print("   Run this script from your project root or a subdirectory")
        sys.exit(1)

    metrics_dir = agent_dir / "metrics"
    
    if not metrics_dir.exists():
        print("📊 No metrics directory found")
        print(f"   Metrics will be created at: {metrics_dir}")
        sys.exit(0)
    
    # Load and analyze metrics
    metrics = load_metrics(metrics_dir, days)
    analyze_metrics(metrics)


if __name__ == "__main__":
    main()
