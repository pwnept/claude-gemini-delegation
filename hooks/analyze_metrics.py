#!/usr/bin/env python3
"""
Analyze delegation metrics to identify optimization opportunities

Usage:
    python analyze_metrics.py [--days N] [--caller claude|codex|agy|all]

Options:
    --days N              Analyze metrics from the last N days (default: 7)
    --caller HARNESS      Search harness home delegation-logs/ dir instead of
                          the repo tree. Use 'all' to aggregate all harnesses.
"""

import sys
import csv
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter
from typing import List, Optional, Tuple

from delegation_caller import CALLER_LOG_DIRS as _CALLER_LOG_DIRS

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


def load_metrics(metrics_dirs: List[Path], days: int) -> List[Tuple[str, str, int, int]]:
    """Load metrics from the last N days across one or more directories."""
    metrics = []
    for metrics_dir in metrics_dirs:
        if not metrics_dir.exists():
            continue
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
    caller = None

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ('-h', '--help'):
            print(__doc__)
            sys.exit(0)
        elif arg == '--days' and i + 1 < len(sys.argv):
            days = int(sys.argv[i + 1])
            i += 2
        elif arg == '--caller' and i + 1 < len(sys.argv):
            caller = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    if caller is not None:
        # Search harness home dirs instead of the repo tree.
        if caller == "all":
            metrics_dirs = list(_CALLER_LOG_DIRS.values())
        elif caller in _CALLER_LOG_DIRS:
            metrics_dirs = [_CALLER_LOG_DIRS[caller]]
        else:
            print(f"❌ Unknown caller {caller!r}. Use: claude, codex, agy, all")
            sys.exit(1)
    else:
        # Default: walk up from cwd to find the repo's agent dir.
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
            print("   Run from your project root, or use --caller to search a harness home.")
            sys.exit(1)
        metrics_dirs = [agent_dir / "metrics"]

    if not any(d.exists() for d in metrics_dirs):
        dirs_str = ", ".join(str(d) for d in metrics_dirs)
        print("📊 No metrics directory found")
        print(f"   Searched: {dirs_str}")
        sys.exit(0)

    metrics = load_metrics(metrics_dirs, days)
    analyze_metrics(metrics)


if __name__ == "__main__":
    main()
