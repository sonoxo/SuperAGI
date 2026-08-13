"""Capability-aware model routing for XuniHub.

The router owns selection and failover policy but not provider credentials or SDKs.
Applications register concrete provider adapters that expose health and inference
functions. This keeps routing deterministic and testable without pretending any
provider is configured or online.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set


class ModelRoutingError(RuntimeError):
    """Raised when no registered provider can safely satisfy a model request."""


HealthCheck = Callable[[], bool]
InferenceCall = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class ModelRequest:
    payload: Mapping[str, Any]
    required_capabilities: Set[str] = field(default_factory=set)
    preferred_provider: Optional[str] = None


@dataclass(frozen=True)
class ProviderAdapter:
    name: str
    priority: int
    capabilities: Set[str]
    health_check: HealthCheck
    infer: InferenceCall

    def supports(self, required: Iterable[str]) -> bool:
        return set(required).issubset(self.capabilities)


@dataclass(frozen=True)
class RoutingAttempt:
    provider: str
    healthy: bool
    attempted: bool
    error: Optional[str] = None


@dataclass(frozen=True)
class RoutedModelResult:
    provider: str
    response: Mapping[str, Any]
    attempts: List[RoutingAttempt]


class ModelRouter:
    """Select the healthiest capable provider and fail over conservatively."""

    def __init__(self) -> None:
        self._providers: Dict[str, ProviderAdapter] = {}

    def register(self, adapter: ProviderAdapter) -> None:
        name = adapter.name.strip()
        if not name:
            raise ValueError("provider name is required")
        if name in self._providers:
            raise ValueError("provider already registered: %s" % name)
        if not callable(adapter.health_check) or not callable(adapter.infer):
            raise TypeError("provider health_check and infer must be callable")
        self._providers[name] = adapter

    def names(self) -> List[str]:
        return [item.name for item in self._ordered()]

    def _ordered(self) -> List[ProviderAdapter]:
        return sorted(self._providers.values(), key=lambda item: (item.priority, item.name))

    def _candidates(self, request: ModelRequest) -> List[ProviderAdapter]:
        capable = [item for item in self._ordered() if item.supports(request.required_capabilities)]
        preferred = request.preferred_provider
        if preferred is None:
            return capable
        for index, item in enumerate(capable):
            if item.name == preferred:
                return [capable[index]] + capable[:index] + capable[index + 1 :]
        return capable

    def route(self, request: ModelRequest) -> RoutedModelResult:
        if not isinstance(request.payload, Mapping):
            raise TypeError("model payload must be an object")
        candidates = self._candidates(request)
        if not candidates:
            raise ModelRoutingError("no registered provider supports required capabilities")

        attempts: List[RoutingAttempt] = []
        for provider in candidates:
            try:
                healthy = bool(provider.health_check())
            except Exception as exc:
                attempts.append(
                    RoutingAttempt(
                        provider=provider.name,
                        healthy=False,
                        attempted=False,
                        error="health_check %s: %s" % (exc.__class__.__name__, exc),
                    )
                )
                continue
            if not healthy:
                attempts.append(RoutingAttempt(provider=provider.name, healthy=False, attempted=False))
                continue
            try:
                response = provider.infer(request.payload)
            except Exception as exc:
                attempts.append(
                    RoutingAttempt(
                        provider=provider.name,
                        healthy=True,
                        attempted=True,
                        error="inference %s: %s" % (exc.__class__.__name__, exc),
                    )
                )
                continue
            if not isinstance(response, Mapping):
                attempts.append(
                    RoutingAttempt(
                        provider=provider.name,
                        healthy=True,
                        attempted=True,
                        error="inference response must be an object",
                    )
                )
                continue
            attempts.append(RoutingAttempt(provider=provider.name, healthy=True, attempted=True))
            return RoutedModelResult(provider=provider.name, response=response, attempts=attempts)

        details = "; ".join(
            "%s=%s" % (attempt.provider, attempt.error or ("unhealthy" if not attempt.healthy else "failed"))
            for attempt in attempts
        )
        raise ModelRoutingError("all capable providers failed: %s" % details)

    def as_model_step(
        self,
        *,
        required_capabilities: Optional[Iterable[str]] = None,
        preferred_provider: Optional[str] = None,
    ) -> Callable[[Dict[str, Any]], Mapping[str, Any]]:
        """Adapt this router to AgentToolLoop's model_step callable contract."""

        required = set(required_capabilities or ())

        def model_step(context: Dict[str, Any]) -> Mapping[str, Any]:
            result = self.route(
                ModelRequest(
                    payload=context,
                    required_capabilities=required,
                    preferred_provider=preferred_provider,
                )
            )
            return result.response

        return model_step
