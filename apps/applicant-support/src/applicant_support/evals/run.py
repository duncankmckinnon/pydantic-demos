from pathlib import Path

from applicant_support.agent import AgentDeps, build_agent
from applicant_support.db import ApplicantDatabase
from applicant_support.evals.dataset import (
    ApplicantSupportEvalInput,
    applicant_support_eval_dataset,
)
from demo_core.logfire_setup import configure_logfire
from demo_core.settings import GatewaySettings


def main() -> None:
    configure_logfire("applicant-support-evals")

    database_path = Path(__file__).resolve().parents[3] / "data" / "evals.sqlite3"
    database = ApplicantDatabase(database_path)
    database.initialize()
    agent = build_agent(GatewaySettings())

    async def run_case(inputs: ApplicantSupportEvalInput) -> str:
        result = await agent.run(
            inputs.question,
            deps=AgentDeps(database=database, application_id=inputs.application_id),
        )
        return str(result.output)

    report = applicant_support_eval_dataset.evaluate_sync(
        run_case,
        max_concurrency=1,
    )
    report.print()


if __name__ == "__main__":
    main()
