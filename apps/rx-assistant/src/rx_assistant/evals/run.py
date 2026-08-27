from demo_core.logfire_setup import configure_logfire
from demo_core.settings import GatewaySettings
from rx_assistant.agent import Deps, build_agent
from rx_assistant.db import create_pool
from rx_assistant.embeddings import load_embedding_model
from rx_assistant.evals.dataset import rx_assistant_eval_dataset
from rx_assistant.settings import DatabaseSettings


async def run_rx_assistant(message: str) -> str:
    gateway_settings = GatewaySettings()
    agent = build_agent(gateway_settings)

    database_settings = DatabaseSettings()
    pool = await create_pool(database_settings.database_url)
    try:
        deps = Deps(pool=pool, embedding_model=load_embedding_model())
        result = await agent.run(message, deps=deps)
        return str(result.output)
    finally:
        await pool.close()


if __name__ == "__main__":
    # The agent is Logfire-instrumented, so configuring here is all it takes for a manual
    # eval run's real model calls to show up in traces — no extra reporting code.
    configure_logfire("rx-assistant-evals")
    report = rx_assistant_eval_dataset.evaluate_sync(run_rx_assistant)
    report.print()
