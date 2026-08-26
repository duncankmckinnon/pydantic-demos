from chat.agent import build_agent
from chat.evals.dataset import chat_eval_dataset
from demo_core.settings import GatewaySettings


async def run_chat(message: str) -> str:
    settings = GatewaySettings()
    agent = build_agent(settings)
    result = await agent.run(message)
    return str(result.output)


if __name__ == "__main__":
    report = chat_eval_dataset.evaluate_sync(run_chat)
    report.print()
