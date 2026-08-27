from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Protocol

from local_dev.core.contracts import ModelRequest


class ProviderNotSentError(RuntimeError):
    """Adapter can prove the request never reached billable provider transport."""


@dataclass(frozen=True, slots=True)
class CostQuote:
    provider: str
    model: str
    worst_case_eur: Decimal
    reference: str

    def __post_init__(self) -> None:
        provider = self.provider.strip()
        model = self.model.strip()
        reference = self.reference.strip()
        if not provider or not model or not reference:
            raise ValueError("provider, model, and quote reference must not be empty")
        _validate_money(self.worst_case_eur, allow_zero=False)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "reference", reference)


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider: str
    model: str
    output_text: str
    actual_cost_eur: Decimal
    provider_request_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider = self.provider.strip()
        model = self.model.strip()
        if not provider or not model:
            raise ValueError("provider and model must not be empty")
        if not isinstance(self.output_text, str):
            raise TypeError("output_text must be a string")
        _validate_money(self.actual_cost_eur, allow_zero=True)
        request_id = self.provider_request_id
        if request_id is not None:
            request_id = request_id.strip()
            if not request_id:
                raise ValueError("provider_request_id must be non-empty when present")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "provider_request_id", request_id)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


class PaidProviderAdapter(Protocol):
    """Provider-specific pricing and transport behind the paid-call gateway."""

    @property
    def name(self) -> str: ...

    def quote(self, request: ModelRequest) -> CostQuote:
        """Return a conservative local worst-case price; must not make a paid call."""
        ...

    def execute(self, request: ModelRequest, quote: CostQuote) -> ProviderResponse:
        """Perform the provider call after the gateway has durably authorized dispatch."""
        ...


def _validate_money(value: Decimal, *, allow_zero: bool) -> None:
    if not isinstance(value, Decimal):
        raise TypeError("monetary values must be Decimal")
    if not value.is_finite():
        raise ValueError("monetary values must be finite")
    if value < 0 or (value == 0 and not allow_zero):
        requirement = "non-negative" if allow_zero else "positive"
        raise ValueError(f"monetary value must be {requirement}")


def _freeze_metadata(metadata: Mapping[str, str]) -> Mapping[str, str]:
    copied: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("metadata keys must be non-empty strings")
        if not isinstance(value, str):
            raise TypeError("metadata values must be strings")
        copied[key] = value
    return MappingProxyType(copied)
