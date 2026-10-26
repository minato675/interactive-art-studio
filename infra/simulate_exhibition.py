"""설계 검토용 가상 8시간 시뮬레이션. 외부 API·UE·생산 상태를 사용하지 않는다."""
import argparse
import json
from pathlib import Path

from studio.exhibition.simulation import scenarios, simulate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initial-artworks", type=int, default=0)
    parser.add_argument("--load-seconds", type=float, default=0)
    parser.add_argument("--all-scenarios", action="store_true")
    parser.add_argument("--output", type=Path, help="선택적 JSON 저장 경로. 기본은 표준 출력만 사용")
    args = parser.parse_args()
    result = scenarios() if args.all_scenarios else simulate(initial_artworks=args.initial_artworks,
                                                            load_seconds=args.load_seconds)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    summary = ({key: {k: v for k, v in value.items() if k not in ("plays", "play_counts")}
                for key, value in result.items()} if args.all_scenarios else
               {k: v for k, v in result.items() if k not in ("plays", "play_counts")})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
