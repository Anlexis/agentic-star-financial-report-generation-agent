"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, the gateway calls agent.invoke() directly.

import json
import os
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory
from src.graph.graph import FinC2007Agent, runtime_config

app = FastAPI(title="Agent")

# The platform registry loads config/config.yaml and passes it as
# Graph(config=...); the standalone server mirrors that exactly, so declared
# runtime parameters are live in both deployments.
agent = FinC2007Agent(config=runtime_config())
agent.compile()
# Namespace and agent name match the manifest values.
agent.provision_secrets(secrets_factory(namespace="fin", agent_name="FinC2007Agent"))

# Coarse adapter-level bound on the serialized structured input. Per-field
# bounds (identifier alphabet, numeric ranges, entry counts) are enforced
# inside the graph by PreProcessNode.
_MAX_INPUT_CONTEXT_BYTES = 262_144


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""
    # Structured caller data channel: the report request may be supplied here
    # instead of, or in addition to, the JSON body in `input`.
    input_context: Optional[Dict[str, Any]] = None


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request) -> Any:
    # Trust promotion: callers presenting a valid INVOKE_AUTH_TOKEN Bearer
    # credential run as VERIFIED_EXTERNAL — the minimum trust PreProcessNode
    # requires. Unauthenticated requests remain ANONYMOUS, and trust already
    # established by upstream middleware is never demoted.
    _env_token = os.environ.get("INVOKE_AUTH_TOKEN", "")
    _auth_header = request.headers.get("Authorization", "")
    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
    if _env_token and _auth_header == f"Bearer {_env_token}":
        trust = TrustLevel.VERIFIED_EXTERNAL

    input_context = req.input_context or {}
    if input_context and len(json.dumps(input_context, default=str)) > _MAX_INPUT_CONTEXT_BYTES:
        raise HTTPException(status_code=413, detail="input_context exceeds the maximum allowed size.")

    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return agent.invoke(req.input, ctx=ctx, input_context=input_context)


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "agent": "FinC2007Agent"}
