import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.evaluation.dataset import REPOSITORY, EvaluationError, load_dataset, select_cases
from app.evaluation.report import build_report, load_adjudication, load_predictions, write_reports
from app.evaluation.runner import evaluation_database_url, isolated_sessions, run_cases
from app.extraction.providers.openai import OpenAIExtractionProvider, ProviderConfigurationError


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Frozen extraction evaluation; live calls are opt-in"
    )
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("validate", "score", "run"):
        command = commands.add_parser(name)
        command.add_argument("--dataset", type=Path, required=True)
        if name != "validate":
            command.add_argument("--output", type=Path)
        if name == "score":
            command.add_argument("--predictions", type=Path, required=True)
            command.add_argument("--adjudication", type=Path)
        if name == "run":
            command.add_argument("--live", action="store_true")
            command.add_argument("--split", choices=("all", "dev", "holdout"), default="all")
            command.add_argument("--case-id", action="append")
    return result


def output_path(requested: Path | None) -> Path:
    root = REPOSITORY / "evals" / "runs"
    output = (requested or root / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")).resolve()
    if not output.is_relative_to(root.resolve()):
        raise EvaluationError("Generated artifacts must stay under gitignored evals/runs")
    if output.exists():
        raise EvaluationError("Output directory already exists; preserve previous runs")
    return output


async def live_run(args: argparse.Namespace) -> None:
    if not args.live:
        raise EvaluationError(
            "Provider requests require explicit --live; use score for offline fixtures"
        )
    dataset = load_dataset(args.dataset)
    cases = select_cases(dataset, args.split, args.case_id)
    database_url = evaluation_database_url()
    provider = OpenAIExtractionProvider()
    output = output_path(args.output)
    async with isolated_sessions(database_url) as sessions:
        print(
            json.dumps(
                {
                    "case_count": len(cases),
                    "provider": provider.provider_name,
                    "model": provider.model,
                    "prompt_version": provider.prompt_version,
                    "dataset_version": dataset.manifest.dataset_version,
                }
            ),
            flush=True,
        )
        artifact = await run_cases(dataset, cases, sessions, provider)
    report = build_report(dataset, artifact)
    write_reports(report, output)
    (output / "predictions.json").write_text(
        artifact.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    print(f"Reports: {output}")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "run":
            asyncio.run(live_run(args))
        else:
            dataset = load_dataset(args.dataset)
            if args.command == "validate":
                print(
                    f"Validated {dataset.manifest.dataset_version}: {len(dataset.cases)} cases, "
                    f"{sum(len(case.gold.findings) for case in dataset.cases)} gold findings"
                )
            else:
                artifact, digest = load_predictions(args.predictions)
                review = load_adjudication(args.adjudication, digest) if args.adjudication else None
                report = build_report(dataset, artifact, review)
                output = output_path(args.output)
                write_reports(report, output)
                print(f"Reports: {output}")
        return 0
    except (EvaluationError, ProviderConfigurationError) as error:
        print(str(error), file=sys.stderr)
    except (OSError, ValidationError, SQLAlchemyError):
        print(
            "Evaluation failed; check files, configuration, and database availability",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
