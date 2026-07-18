from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.prompt_impact import PromptImpactReporter


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare prompt impact between two runs")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = PromptImpactReporter().compare(args.baseline, args.candidate)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
