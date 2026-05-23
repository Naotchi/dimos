"""The local-tts agentic blueprint must NOT bake `model` into the TimedMcpClient atom.

Model is owned by the profile config.json (category A); baking it here would
re-introduce a blueprint↔profile collision (Spec §1).
"""


def test_timed_mcp_client_atom_has_no_model():
    from dimos.agents.mcp.mcp_client_ja import TimedMcpClient
    from dimos.robot.unitree.go2.blueprints.agentic.unitree_go2_agentic_local_tts import (
        unitree_go2_agentic_local_tts as bp,
    )

    atom = next(b for b in bp.blueprints if b.module is TimedMcpClient)
    assert "model" not in atom.kwargs
