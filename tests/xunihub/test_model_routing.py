import pytest

from superagi.xunihub.model_routing import (
    ModelRequest,
    ModelRouter,
    ModelRoutingError,
    ProviderAdapter,
)


def provider(name, priority, healthy=True, capabilities=None, response=None, error=None):
    def health_check():
        return healthy

    def infer(payload):
        if error:
            raise RuntimeError(error)
        return response or {"final": "%s:%s" % (name, payload.get("objective", "ok"))}

    return ProviderAdapter(
        name=name,
        priority=priority,
        capabilities=set(capabilities or ()),
        health_check=health_check,
        infer=infer,
    )


def test_router_prefers_lowest_priority_number():
    router = ModelRouter()
    router.register(provider("secondary", 20, capabilities={"tools"}))
    router.register(provider("primary", 10, capabilities={"tools"}))

    result = router.route(ModelRequest(payload={"objective": "build"}, required_capabilities={"tools"}))
    assert result.provider == "primary"
    assert result.response["final"] == "primary:build"


def test_router_skips_unhealthy_and_fails_over():
    router = ModelRouter()
    router.register(provider("primary", 10, healthy=False, capabilities={"tools"}))
    router.register(provider("secondary", 20, capabilities={"tools"}))

    result = router.route(ModelRequest(payload={"objective": "repair"}, required_capabilities={"tools"}))
    assert result.provider == "secondary"
    assert result.attempts[0].provider == "primary"
    assert result.attempts[0].attempted is False


def test_router_fails_over_after_inference_error():
    router = ModelRouter()
    router.register(provider("primary", 10, capabilities={"tools"}, error="temporary"))
    router.register(provider("secondary", 20, capabilities={"tools"}))

    result = router.route(ModelRequest(payload={"objective": "continue"}, required_capabilities={"tools"}))
    assert result.provider == "secondary"
    assert "temporary" in result.attempts[0].error


def test_preferred_provider_moves_a_capable_provider_first():
    router = ModelRouter()
    router.register(provider("primary", 10, capabilities={"tools"}))
    router.register(provider("secondary", 20, capabilities={"tools"}))

    result = router.route(
        ModelRequest(
            payload={"objective": "build"},
            required_capabilities={"tools"},
            preferred_provider="secondary",
        )
    )
    assert result.provider == "secondary"


def test_capability_filter_never_routes_to_incapable_provider():
    router = ModelRouter()
    router.register(provider("text", 1, capabilities={"text"}))
    router.register(provider("tools", 2, capabilities={"text", "tools"}))

    result = router.route(ModelRequest(payload={}, required_capabilities={"tools"}))
    assert result.provider == "tools"


def test_router_reports_no_capable_provider():
    router = ModelRouter()
    router.register(provider("text", 1, capabilities={"text"}))

    with pytest.raises(ModelRoutingError, match="supports required capabilities"):
        router.route(ModelRequest(payload={}, required_capabilities={"tools"}))


def test_router_adapts_to_agent_model_step_contract():
    router = ModelRouter()
    router.register(provider("primary", 1, capabilities={"tools"}, response={"final": "done"}))

    model_step = router.as_model_step(required_capabilities={"tools"})
    assert model_step({"objective": "anything"}) == {"final": "done"}
