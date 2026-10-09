"""Opt-in enterprise Foundry example; requires configured Azure Foundry credentials.

This is illustrative, NOT a live test and does not execute real payments.
Requires: pip install agent-framework-foundry azure-identity
"""
import asyncio
from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from azure.identity.aio import AzureCliCredential
from loopgrid_microsoft_agent import LoopGridMicrosoftAgent

async def main():
    bridge = LoopGridMicrosoftAgent(workspace_id="YOUR_WORKSPACE_ID", agent_id="refund-agent")
    @tool(approval_mode="never_require")
    def sandbox_refund(amount: int) -> str:
        """Simulate a refund without modifying any external payment system."""
        return f"Sandbox refund amount {amount} only; real_money_moved=false"

    decision = bridge.start_decision(
        decision_type="sandbox_refund",
        agent={"id": "refund-agent"},
        authority={"acting_for": "sandbox", "scope": ["refund:simulate"]},
        model={"provider": "azure-foundry", "name": "YOUR_CONFIGURED_FOUNDRY_MODEL"},
        context={"prompt_version": "refund-demo-v1"},
        proposed_action={"tool": "sandbox_refund", "arguments": {"amount": 25}},
        # This must be an AUTHENTIC policy outcome from your application.
        policy={"policy_id": "your-sandbox-policy", "version": "1", "decision": "auto_allowed"},
        metadata={"sandbox": True, "real_money_moved": False},
    )
    async with AzureCliCredential() as credential:
        async with Agent(
            client=FoundryChatClient(credential=credential),
            name="RefundDemo", instructions="Call sandbox_refund with amount 25 exactly once.",
            tools=[sandbox_refund], middleware=bridge.middleware,
        ) as agent:
            with bridge.decision_context(decision["decision_id"]):
                result = await agent.run("Run the sandbox refund for 25, then summarize.")
    print(result.text)
    # Do not call record_outcome() here: simulated text is not an authoritative receipt.
    print(bridge.get_decision(decision["decision_id"])["verification"])

if __name__ == "__main__":
    asyncio.run(main())
