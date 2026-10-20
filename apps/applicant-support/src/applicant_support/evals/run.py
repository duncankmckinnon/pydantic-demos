import argparse
import asyncio

from applicant_support.config import resolve_agent_config
from applicant_support.evals.experiments import (
    ExperimentKind,
    build_experiment_request,
    run_comparison,
)
from applicant_support.evals.hosted import create_dataset_client, load_hosted_dataset
from demo_core.logfire_setup import configure_logfire
from demo_core.settings import GatewaySettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run applicant-support Logfire experiments.")
    parser.add_argument("--kind", choices=("model", "prompt"), required=True)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--smoke",
        action="store_true",
        help="Run three representative hosted cases (the default).",
    )
    scope.add_argument(
        "--full",
        action="store_true",
        help="Explicitly run every hosted case for every candidate.",
    )
    return parser


async def _run(kind: ExperimentKind, *, full: bool) -> None:
    configure_logfire("applicant-support-evals")
    with create_dataset_client() as client:
        hosted = load_hosted_dataset(client)

    dataset = hosted
    if not full:
        from applicant_support.evals.experiments import _smoke_dataset

        dataset = _smoke_dataset(hosted)

    request = build_experiment_request(kind, control=resolve_agent_config())
    print(
        f"Starting {kind} {'full' if full else 'smoke'} comparison: "
        f"{len(request.candidates)} candidates x {len(dataset.cases)} cases."
    )
    summaries = await run_comparison(
        request,
        dataset=dataset,
        gateway_settings=GatewaySettings(),
    )
    for summary in summaries:
        print(
            f"Completed {summary.candidate}: {summary.case_count} cases as "
            f"{summary.experiment_name}."
        )


def main() -> None:
    args = _parser().parse_args()
    asyncio.run(_run(args.kind, full=args.full))


if __name__ == "__main__":
    main()
