#!/usr/bin/env python3
"""
Post-delegation hook for Claude Code/Codex -> agy delegation
Validates agy's response quality and logs metrics

Usage:
    python post_delegate.py <response> [max_lines] [task_context]
    
Example:
    python post_delegate.py "Response text here" 10 "dependency-analysis"
""" 

import os
import sys
import re
import csv
from datetime import datetime
from pathlib import Path
from typing import Tuple

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def count_lines(text: str) -> int:
    """Count actual lines in response."""
    return len([line for line in text.split('\n') if line.strip()])


def estimate_tokens(text: str) -> int:
    """Estimate token count (rough: 1 token ≈ 4 characters)."""
    return len(text) // 4


def validate_response(response: str, max_lines: int) -> Tuple[bool, list]:
    """
    Validate response quality.
    Returns (is_valid, warnings)
    """
    warnings = []
    actual_lines = count_lines(response)
    token_estimate = estimate_tokens(response)
    
    # Check if response is within limits
    if actual_lines > max_lines:
        warnings.append(
            f"⚠️  WARNING: Response too long ({actual_lines} lines > {max_lines} expected)"
        )
        warnings.append("   Suggestion: Add stricter compression directive to prompt")
    
    # Check if response is too brief (might be missing context)
    if actual_lines < 3:
        warnings.append(f"⚠️  WARNING: Response very brief ({actual_lines} lines)")
        warnings.append("   Suggestion: Check if agy understood the task")
    
    # Check token efficiency
    if token_estimate > 1000:
        warnings.append(f"⚠️  WARNING: Response uses ~{token_estimate} tokens (>1000)")
        warnings.append("   Suggestion: Refine prompt compression directives")
    
    # Success message if no warnings
    if not warnings:
        print(f"✅ Response quality: {actual_lines} lines, ~{token_estimate} tokens")
        return True, []
    
    return False, warnings


def log_metrics(task: str, lines: int, tokens: int, metrics_dir: Path):
    """Log metrics for analysis."""
    metrics_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date = datetime.now().strftime("%Y-%m-%d")
    log_file = metrics_dir / f"delegation-{date}.csv"
    
    # Create header if file doesn't exist
    if not log_file.exists():
        with log_file.open('w', encoding="utf-8", newline="") as f:
            f.write("timestamp,task,lines,tokens\n")
    
    # Append metrics
    with log_file.open('a', encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, task, lines, tokens])


def extract_action_items(response: str) -> list:
    """Extract actionable items from response."""
    patterns = [
        r'CRITICAL:?\s*(.+)',
        r'TODO:?\s*(.+)',
        r'FIXME:?\s*(.+)',
        r'Action:?\s*(.+)',
        r'Recommend(?:ed)?:?\s*(.+)',
        r'Next step:?\s*(.+)',
    ]
    
    action_items = []
    for pattern in patterns:
        matches = re.finditer(pattern, response, re.IGNORECASE | re.MULTILINE)
        action_items.extend([m.group(0) for m in matches])
    
    return action_items


def check_daily_usage(metrics_dir: Path) -> int:
    """Check how many delegations were made today."""
    date = datetime.now().strftime("%Y-%m-%d")
    log_file = metrics_dir / f"delegation-{date}.csv"
    
    if not log_file.exists():
        return 0
    
    with log_file.open('r', encoding="utf-8", newline="") as f:
        # Subtract 1 for header row
        return max(0, len(f.readlines()) - 1)


def main():
    """Main execution."""
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == '--input-file':
        if len(sys.argv) < 3:
            print("--input-file requires a path argument", file=sys.stderr)
            sys.exit(1)
        from pathlib import Path as _Path
        response = _Path(sys.argv[2]).read_text(encoding='utf-8', errors='replace')
        max_lines = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        task_context = sys.argv[4] if len(sys.argv) > 4 else "unknown"
    else:
        response = sys.argv[1]
        max_lines = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        task_context = sys.argv[3] if len(sys.argv) > 3 else "unknown"
    
    # Determine metrics directory
    agent_dir = None
    current_dir = Path.cwd()

    # 1. Respect DELEGATION_HOOK_PREFIX env var set by per-env shim
    hook_prefix = os.environ.get("DELEGATION_HOOK_PREFIX")
    if hook_prefix:
        prefix_path = Path(hook_prefix)
        if prefix_path.parent.name in (".claude", ".codex", ".Codex"):
            agent_dir = prefix_path.parent

    # 2. Search up the tree (.gemini-delegation preferred over .claude)
    if agent_dir is None:
        for directory in (current_dir, *current_dir.parents):
            for name in (".gemini-delegation", ".claude"):
                candidate = directory / name
                if candidate.exists():
                    agent_dir = candidate
                    break
            if agent_dir:
                break

    if agent_dir is None:
        agent_dir = current_dir / ".gemini-delegation"

    metrics_dir = agent_dir / "metrics"
    
    # Validate response
    actual_lines = count_lines(response)
    token_estimate = estimate_tokens(response)
    
    is_valid, warnings = validate_response(response, max_lines)
    
    # Print warnings if any
    for warning in warnings:
        print(warning)
    
    # Log metrics
    log_metrics(task_context, actual_lines, token_estimate, metrics_dir)
    
    # Extract and display action items
    action_items = extract_action_items(response)
    if action_items:
        print("\n📋 Action Items Found:")
        for item in action_items:
            print(f"   {item}")
    
    # Check daily usage and suggest analysis
    daily_count = check_daily_usage(metrics_dir)
    if daily_count >= 20:
        print(f"\n💡 TIP: You've made {daily_count} delegations today.")
        print("   Run 'python .gemini-delegation/hooks/analyze_metrics.py' to see optimization opportunities")
    
    # Exit with appropriate code
    sys.exit(0 if is_valid else 1)


if __name__ == "__main__":
    main()
