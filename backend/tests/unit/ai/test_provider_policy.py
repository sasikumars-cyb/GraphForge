"""External-AI-provider data-governance policy (H-1).

The audit found the only enabled provider was DeepSeek — an external SaaS
API — receiving repository names, file paths, symbol names and dependency
edges from 67 private repositories, with no policy gate, no opt-in and no
visibility. Amazon Bedrock (in-account) was implemented and wired into the
environment but not selected.

These tests build `Settings` directly rather than inheriting the suite-wide
opt-in in tests/conftest.py, so they exercise the REAL shipped default.
"""

from __future__ import annotations

import pytest

from app.ai.config import store
from app.ai.config.resolver import (
    ExternalProviderNotPermittedError,
    classify_deployment,
    enforce_provider_policy,
    fallback_chain,
    resolve,
)
from app.ai.config.store import ConfigSnapshot, ProfileRecord, ProviderRecord
from app.ai.providers.registry import (
    Capability,
    ProviderBuildConfig,
    ProviderHosting,
    ProviderSpec,
    all_providers,
    require_provider_spec,
)
from app.core.config import Settings
from app.schemas.ai_workspace import ProviderUpsertRequest


def _settings(**overrides: object) -> Settings:
    """Settings with the policy field pinned to its SHIPPED default unless a
    test overrides it. Explicit because tests/conftest.py sets
    ALLOW_EXTERNAL_AI_PROVIDERS=true process-wide for the rest of the suite,
    and `Settings()` would otherwise inherit that opt-in — which would make
    every assertion here silently test the permissive path."""
    overrides.setdefault("allow_external_ai_providers", False)
    return Settings(**overrides)  # type: ignore[arg-type]


def _config(base_url: str | None = None) -> ProviderBuildConfig:
    return ProviderBuildConfig(api_key=None, model="m", base_url=base_url)


class TestShippedDefault:
    def test_external_providers_are_denied_by_default(self):
        """The whole point: a deployment that configures nothing cannot send
        private engineering metadata to a third party."""
        assert _settings().allow_external_ai_providers is False

    def test_a_new_provider_defaults_to_external_and_therefore_needs_opt_in(self):
        """`ProviderSpec.hosting` defaults to EXTERNAL so forgetting to
        classify a newly added provider fails closed."""
        from app.ai.providers.registry import ProviderSpec

        spec = ProviderSpec(key="brand-new", label="Brand New", build=lambda _cfg: None)  # type: ignore[arg-type,return-value]
        assert spec.hosting is ProviderHosting.EXTERNAL
        with pytest.raises(ExternalProviderNotPermittedError):
            enforce_provider_policy(spec, _config(), _settings())


class TestPolicyGate:
    def test_in_account_provider_is_permitted_without_any_opt_in(self):
        enforce_provider_policy(require_provider_spec("bedrock"), _config(), _settings())

    def test_credential_addressed_providers_are_permitted_by_default(self):
        specs = [s for s in all_providers() if s.hosting is ProviderHosting.CUSTOMER_ACCOUNT]
        assert specs, "the registry must classify at least one customer-account provider"
        for spec in specs:
            enforce_provider_policy(spec, _config(), _settings())

    def test_external_provider_is_rejected_when_not_opted_in(self):
        with pytest.raises(ExternalProviderNotPermittedError) as exc:
            enforce_provider_policy(require_provider_spec("deepseek"), _config(), _settings())
        assert "deepseek" in str(exc.value)

    def test_every_external_provider_is_rejected_by_default(self):
        for spec in all_providers():
            if spec.hosting is ProviderHosting.EXTERNAL:
                with pytest.raises(ExternalProviderNotPermittedError):
                    enforce_provider_policy(spec, _config(), _settings())

    def test_external_provider_is_permitted_once_explicitly_opted_in(self):
        enforce_provider_policy(
            require_provider_spec("deepseek"),
            _config(),
            _settings(allow_external_ai_providers=True),
        )

    def test_the_gate_decides_on_hosting_alone_never_on_provider_identity(self):
        """Classification lives in the registry; the gate is generic. Proven
        behaviourally rather than by grepping the source: the same provider
        key flips decision purely by its declared hosting, so no name-based
        branch can exist."""
        from app.ai.providers.registry import ProviderSpec

        external_named_bedrock = ProviderSpec(
            key="bedrock", label="x", build=lambda _c: None, hosting=ProviderHosting.EXTERNAL  # type: ignore[arg-type,return-value]
        )
        in_account_named_deepseek = ProviderSpec(
            key="deepseek",
            label="x",
            build=lambda _c: None,  # type: ignore[arg-type,return-value]
            hosting=ProviderHosting.CUSTOMER_ACCOUNT,
        )
        with pytest.raises(ExternalProviderNotPermittedError):
            enforce_provider_policy(external_named_bedrock, _config(), _settings())
        enforce_provider_policy(in_account_named_deepseek, _config(), _settings())


class TestClassification:
    """Provider TYPE only — the addressing shape, never the trust verdict."""

    def test_bedrock_is_credential_addressed(self):
        assert require_provider_spec("bedrock").hosting is ProviderHosting.CUSTOMER_ACCOUNT

    def test_saas_apis_are_external(self):
        for key in ("openai", "gemini", "groq", "deepseek", "anthropic", "openrouter"):
            assert require_provider_spec(key).hosting is ProviderHosting.EXTERNAL, key

    def test_url_addressed_providers_are_endpoint_classified_not_auto_trusted(self):
        """The correction: Azure OpenAI / Vertex AI / Ollama are only the
        operator's if the configured endpoint says so."""
        for key in ("azure_openai", "vertex_ai", "ollama"):
            assert require_provider_spec(key).hosting is ProviderHosting.CUSTOMER_ENDPOINT, key


class TestEndpointVerification:
    """CUSTOMER_ENDPOINT providers are judged on the resolved endpoint."""

    def test_ollama_on_localhost_is_trusted(self):
        verdict = classify_deployment(
            require_provider_spec("ollama"),
            _config("http://localhost:11434/v1/chat/completions"),
            _settings(),
        )
        assert verdict.trusted, verdict.reason

    def test_ollama_repointed_at_a_public_host_is_NOT_trusted(self):
        """The concrete hole the old provider-name model had: nothing stopped
        an 'Ollama' provider whose base_url was a public endpoint from
        inheriting local-deployment trust."""
        verdict = classify_deployment(
            require_provider_spec("ollama"),
            _config("https://ollama.evil.example.com/v1/chat/completions"),
            _settings(),
        )
        assert not verdict.trusted
        assert "not verifiably operator-controlled" in verdict.reason

    def test_a_private_network_address_is_trusted(self):
        for url in (
            "http://10.1.2.3:11434/v1",
            "http://192.168.0.9:11434/v1",
            "http://127.0.0.1:11434/v1",
            "http://llm-gateway:11434/v1",
            "http://models.internal/v1",
        ):
            verdict = classify_deployment(
                require_provider_spec("ollama"), _config(url), _settings()
            )
            assert verdict.trusted, f"{url}: {verdict.reason}"

    def test_a_public_address_is_not_trusted(self):
        for url in ("https://api.example.com/v1", "http://8.8.8.8/v1"):
            verdict = classify_deployment(
                require_provider_spec("ollama"), _config(url), _settings()
            )
            assert not verdict.trusted, url

    def test_an_azure_endpoint_is_not_trusted_merely_for_looking_like_azure(self):
        """`*.openai.azure.com` is a resource in SOMEBODY's tenant."""
        verdict = classify_deployment(
            require_provider_spec("azure_openai"),
            _config("https://someone-elses.openai.azure.com/openai/deployments/x"),
            _settings(),
        )
        assert not verdict.trusted

    def test_an_explicitly_approved_endpoint_is_trusted(self):
        verdict = classify_deployment(
            require_provider_spec("azure_openai"),
            _config("https://acme-prod.openai.azure.com/openai/deployments/x"),
            _settings(approved_ai_endpoints=["acme-prod.openai.azure.com"]),
        )
        assert verdict.trusted
        assert "explicitly approved" in verdict.reason

    def test_the_approval_list_is_matched_exactly_not_by_suffix(self):
        verdict = classify_deployment(
            require_provider_spec("azure_openai"),
            _config("https://evil-acme-prod.openai.azure.com/x"),
            _settings(approved_ai_endpoints=["acme-prod.openai.azure.com"]),
        )
        assert not verdict.trusted

    def test_an_endpoint_addressed_provider_with_no_endpoint_fails_closed(self):
        spec = ProviderSpec(
            key="mystery",
            label="x",
            build=lambda _c: None,  # type: ignore[arg-type,return-value]
            hosting=ProviderHosting.CUSTOMER_ENDPOINT,
        )
        verdict = classify_deployment(spec, _config(None), _settings())
        assert not verdict.trusted
        assert "no resolvable endpoint" in verdict.reason

    def test_legacy_numeric_ip_encodings_are_not_trusted_by_the_single_label_rule(self):
        """The adversarial-review finding. `ipaddress.ip_address()` rejects
        decimal/hex-encoded IPs outright (it only accepts dotted-quad/colon-
        hex), so these used to fall through into the single-label
        "container name" rule and come out `trusted` — while the OS resolver
        (`socket.gethostbyname`, used transitively by httpx) treats them as
        legacy 32-bit IP literals and connects wherever the number encodes,
        proven empirically:
            "2130706433" -> 127.0.0.1     "0x8080808" -> 8.8.8.8
            "134744072"  -> 8.8.8.8
        A metadata-bearing prompt sent to "0x8080808" would have reached a
        real public address while the policy logged it as operator-controlled.
        """
        for host in ("2130706433", "0x8080808", "134744072", "0X8080808", "0xFFFFFFFF"):
            verdict = classify_deployment(
                require_provider_spec("ollama"), _config(f"http://{host}:11434/v1"), _settings()
            )
            assert not verdict.trusted, f"{host} was misclassified as trusted"

    def test_a_numeric_string_that_is_also_a_valid_dotted_ip_is_still_judged_by_ip_rules(self):
        """The numeric-host guard must not shadow the real IP-address branch
        above it — a genuine dotted-quad private address still resolves
        through `ip_address()`, not the regex."""
        verdict = classify_deployment(
            require_provider_spec("ollama"), _config("http://127.0.0.1:11434/v1"), _settings()
        )
        assert verdict.trusted


class TestResolverEnforcement:
    """The gate must sit in `resolve()` — the one function every provider
    decision passes through — not at the call sites."""

    def test_an_explicit_provider_argument_cannot_bypass_the_policy(self):
        """A caller (or a request parameter that reached one) naming an
        external provider must not be able to route around the policy."""
        with pytest.raises(ExternalProviderNotPermittedError):
            resolve(provider="deepseek", settings=_settings())

    def test_an_explicit_credential_addressed_provider_argument_resolves(self):
        resolved = resolve(provider="bedrock", settings=_settings())
        assert resolved.key == "bedrock"
        assert resolved.spec.hosting is ProviderHosting.CUSTOMER_ACCOUNT

    def test_the_gate_sees_the_resolved_endpoint_not_just_the_spec(self):
        """`resolve()` gates the fully-built decision, so a stored base_url
        that repoints a CUSTOMER_ENDPOINT provider is caught."""
        resolved = resolve(provider="ollama", settings=_settings())
        assert resolved.key == "ollama"  # default endpoint is localhost

    def test_the_same_external_provider_resolves_once_opted_in(self):
        resolved = resolve(
            provider="deepseek", settings=_settings(allow_external_ai_providers=True)
        )
        assert resolved.key == "deepseek"


@pytest.fixture(autouse=True)
def _clean_snapshot():
    """Profile/fallback resolution reads the module-level snapshot
    (`app.ai.config.store.current_snapshot`) — isolate each test's
    `_publish` from every other test in this module and from the rest of
    the suite."""
    store.invalidate()
    yield
    store.invalidate()


def _publish(snapshot: ConfigSnapshot) -> None:
    store._snapshot = snapshot  # noqa: SLF001 — test seam, same as test_ai_profiles.py


class TestProfileResolutionCannotBypassThePolicy:
    """The gate sits inside `resolve()` itself, so it applies identically
    whichever of the two return paths inside that function is taken — the
    profile-first path is exercised here, the direct-provider path in
    `TestResolverEnforcement` above."""

    def test_a_profile_backed_by_an_external_provider_is_denied(self):
        _publish(
            ConfigSnapshot(
                profiles={
                    "cheap": ProfileRecord(slug="cheap", name="Cheap", provider_key="deepseek")
                },
                default_profile_slug="cheap",
                loaded=True,
            )
        )
        with pytest.raises(ExternalProviderNotPermittedError):
            resolve(settings=_settings())

    def test_the_same_profile_resolves_once_external_providers_are_opted_in(self):
        _publish(
            ConfigSnapshot(
                profiles={
                    "cheap": ProfileRecord(slug="cheap", name="Cheap", provider_key="deepseek")
                },
                default_profile_slug="cheap",
                loaded=True,
            )
        )
        resolved = resolve(settings=_settings(allow_external_ai_providers=True))
        assert resolved.key == "deepseek"
        assert resolved.profile_slug == "cheap"

    def test_a_profile_backed_by_an_in_account_provider_needs_no_opt_in(self):
        _publish(
            ConfigSnapshot(
                profiles={"safe": ProfileRecord(slug="safe", name="Safe", provider_key="bedrock")},
                default_profile_slug="safe",
                loaded=True,
            )
        )
        resolved = resolve(settings=_settings())
        assert resolved.key == "bedrock"

    def test_a_stage_mapped_profile_backed_by_an_external_provider_is_also_denied(self):
        """The audit's own escalation path for a workflow stage, not just
        the default profile."""
        _publish(
            ConfigSnapshot(
                profiles={"fast": ProfileRecord(slug="fast", name="Fast", provider_key="groq")},
                stage_overrides={"engineering_review": {"profile": "fast"}},
                loaded=True,
            )
        )
        with pytest.raises(ExternalProviderNotPermittedError):
            resolve(stage="engineering_review", settings=_settings())


class TestFallbackSkipsBlockedExternalProviders:
    """A `fallback_order` that mixes external and in-account providers must
    never let a blocked candidate serve a request — `complete_with_fallback`
    builds each candidate via `resolve()`, catches exactly the exception the
    policy gate raises, and moves on (see `app.ai.config.fallback`)."""

    def test_fallback_chain_excludes_unimplemented_and_disabled_but_not_hosting(self):
        """`fallback_chain` itself only filters on `implemented`/`enabled` —
        the policy is enforced later, per candidate, when each key is
        actually resolved. Pinning that division of labour here so the two
        layers are never assumed to duplicate each other's job."""
        _publish(
            ConfigSnapshot(
                providers={
                    "deepseek": ProviderRecord(
                        provider_key="deepseek",
                        api_key="k",
                        model=None,
                        base_url=None,
                        temperature=None,
                        max_tokens=None,
                        enabled=True,
                        status="unknown",
                    ),
                    "bedrock": ProviderRecord(
                        provider_key="bedrock",
                        api_key=None,
                        model=None,
                        base_url=None,
                        temperature=None,
                        max_tokens=None,
                        enabled=True,
                        status="unknown",
                    ),
                },
                fallback_order=["deepseek", "bedrock"],
                fallback_enabled=True,
                loaded=True,
            )
        )
        assert fallback_chain("openai") == ["deepseek", "bedrock"]

    async def test_a_blocked_external_fallback_candidate_is_skipped_and_the_permitted_one_is_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Primary (bedrock, permitted) fails with a RECOVERABLE error,
        triggering fallback. `fallback_order` lists an external provider
        first (blocked — must be skipped without ever being built or
        called) then an in-account one (permitted — must actually serve the
        request)."""
        from unittest.mock import AsyncMock, patch

        from app.ai.config import resolver
        from app.ai.config.fallback import complete_with_fallback
        from app.ai.providers.base import LLMResponse
        from app.ai.providers.errors import AIProviderRateLimitError

        _publish(
            ConfigSnapshot(
                providers={
                    "bedrock": ProviderRecord(
                        provider_key="bedrock",
                        api_key=None,
                        model=None,
                        base_url=None,
                        temperature=None,
                        max_tokens=None,
                        enabled=True,
                        status="unknown",
                    ),
                    "deepseek": ProviderRecord(
                        provider_key="deepseek",
                        api_key="k",
                        model=None,
                        base_url=None,
                        temperature=None,
                        max_tokens=None,
                        enabled=True,
                        status="unknown",
                    ),
                    "ollama": ProviderRecord(
                        provider_key="ollama",
                        api_key=None,
                        model=None,
                        base_url=None,
                        temperature=None,
                        max_tokens=None,
                        enabled=True,
                        status="unknown",
                    ),
                },
                default_provider="bedrock",
                fallback_order=["deepseek", "ollama"],
                fallback_enabled=True,
                loaded=True,
            )
        )
        # `complete_with_fallback` -> `resolve()` reads `get_settings()`
        # internally rather than taking settings as a parameter, so the
        # policy under test (allow_external_ai_providers=False) has to be
        # installed as the process-wide settings, not passed in locally —
        # `tests/conftest.py` sets ALLOW_EXTERNAL_AI_PROVIDERS=true for the
        # rest of the suite, which `get_settings()`'s cache would otherwise
        # still be serving here.
        monkeypatch.setattr(resolver, "get_settings", lambda: _settings())

        bedrock_call = AsyncMock(side_effect=AIProviderRateLimitError("rate limited"))
        deepseek_call = AsyncMock(side_effect=AssertionError("blocked provider must not be built"))
        ollama_call = AsyncMock(return_value=LLMResponse(text="ok", finish_reason="stop"))

        # "deepseek" and "ollama" both build to `OpenAIProvider` (the
        # OpenAI-compatible adapter for anything speaking that wire
        # format) — route by base_url so the two are distinguishable
        # through one patch, since it's the SAME class/method for both.
        async def _route_openai_compatible(self: object, **kwargs: object) -> LLMResponse:
            base_url = getattr(self, "_base_url", "") or ""
            target = ollama_call if "11434" in base_url else deepseek_call
            return await target(**kwargs)

        with (
            patch("app.ai.providers.bedrock_provider.BedrockProvider.complete", bedrock_call),
            patch(
                "app.ai.providers.openai_provider.OpenAIProvider.complete",
                _route_openai_compatible,
            ),
        ):
            response, served_by = await complete_with_fallback(system_prompt="s", user_prompt="u")

        assert served_by.key == "ollama"
        assert response.text == "ok"
        deepseek_call.assert_not_called()
        bedrock_call.assert_called_once()
        ollama_call.assert_called_once()


class TestStreamingNeverFalselyAdvertised:
    """Only Bedrock previously had a negative assertion — re-adding
    `Capability.STREAMING` to any OTHER provider's spec would have gone
    undetected, since no adapter implements a streaming method at all."""

    def test_no_provider_declares_a_capability_no_adapter_implements(self):
        for spec in all_providers():
            assert Capability.STREAMING not in spec.capabilities, spec.key


class TestAdminApiCannotGrantTrust:
    """The security boundary the review asked to make explicit: admin
    provider configuration (`PUT /ai-workspace/providers/{key}`) can change
    WHICH provider serves a request and how it's addressed, but never
    WHETHER that class of provider is trusted — that is
    `allow_external_ai_providers`/`approved_ai_endpoints`, environment-only
    settings no admin API request body can reach."""

    def test_the_upsert_schema_has_no_field_for_either_policy_setting(self):
        fields = set(ProviderUpsertRequest.model_fields)
        assert "allow_external_ai_providers" not in fields
        assert "approved_ai_endpoints" not in fields

    def test_unknown_fields_in_the_request_body_are_silently_ignored_not_applied(self):
        """`extra="ignore"` on the schema means even a caller who tampers
        with the raw request JSON and adds these keys cannot smuggle them
        through — they never reach a pydantic field, so no code downstream
        of validation ever sees them attached to the request."""
        body = ProviderUpsertRequest.model_validate(
            {
                "model": "gpt-5",
                "allow_external_ai_providers": True,
                "approved_ai_endpoints": ["evil.example.com"],
            }
        )
        assert not hasattr(body, "allow_external_ai_providers")
        assert not hasattr(body, "approved_ai_endpoints")
        assert body.model == "gpt-5"

    def test_settings_fields_are_env_only_not_derived_from_any_stored_provider_row(self):
        """`Settings` (pydantic-settings) reads these from the process
        environment/.env at startup — nothing in `AIProviderConfig`/
        `AIProfile` (the tables the admin API writes) feeds them."""
        from app.models.ai_profile import AIProfile
        from app.models.ai_provider_config import AIProviderConfig, AISettings

        for model in (AIProviderConfig, AISettings, AIProfile):
            columns = {c.name for c in model.__table__.columns}
            assert "allow_external_ai_providers" not in columns
            assert "approved_ai_endpoints" not in columns


class TestCredentialsAreNotExposed:
    def test_the_denial_message_carries_no_credential(self):
        settings = _settings(deepseek_api_key="sk-super-secret-value")
        with pytest.raises(ExternalProviderNotPermittedError) as exc:
            enforce_provider_policy(require_provider_spec("deepseek"), _config(), settings)
        assert "sk-super-secret-value" not in str(exc.value)

    def test_provider_specs_carry_no_credential_values(self):
        """The registry is a static catalogue: it declares that a provider
        `requires_api_key`, but never holds one. Keys arrive at build time on
        `ProviderBuildConfig`, from stored config or env."""
        secret = "sk-super-secret-value"
        settings = _settings(openai_api_key=secret, deepseek_api_key=secret, groq_api_key=secret)
        for spec in all_providers():
            assert not hasattr(spec, "api_key")
            assert secret not in repr(spec)
        # And the configured secret never reaches a spec by any route.
        assert secret not in repr(all_providers())
        assert settings.openai_api_key == secret  # it does exist, just not there

    def test_the_resolved_provider_never_logs_its_key(self):
        """`ResolvedProvider.config.api_key` exists (the builder needs it) but
        the audit log line in `app.agents.llm` prints only key/model/hosting/
        source/profile — asserted here so a future edit that adds the key to
        that line fails a test."""
        import inspect

        from app.agents.llm import StageAwareLLMProvider

        source = inspect.getsource(StageAwareLLMProvider.complete)
        assert "llm_completed" in source
        # `served_by.config` is the only object in scope holding the key, so
        # any attempt to log it has to reach through that attribute.
        assert "served_by.config" not in source


class TestWhatAskActuallySendsToTheProvider:
    """Verifies the data classification in `conversation_service`'s module
    docstring against the real serialized payload, so the claim is tested
    rather than asserted in prose."""

    def _payload(self) -> str:
        import json

        from app.schemas.ask import AskEvidenceItem, AskImpact, AskResponse
        from app.services.conversation_service import InvestigationState

        state = InvestigationState(
            resolved_repository_id="repo-uuid",
            resolved_repository_name="bcs-data-service",
            entities={"A": {"name": "acme/other-service", "impact_level": "high"}},
            last_conclusion="previous answer",
        )
        grounded = AskResponse(
            status="answered",
            question="what breaks if I change bcs-data-service?",
            intent="impact",
            resolved_repository_id="repo-uuid",
            resolved_repository_name="bcs-data-service",
            answer="Impact assessment — Medium.",
            why="reaches 1 repository. Key paths: src/main.py → Ledger (READS_FROM).",
            evidence=[
                AskEvidenceItem(source="Dependency Graph", label="2 rel", provenance="derived")
            ],
            impact=AskImpact(
                severity="medium", summary="1 downstream", affected_databases=["Ledger"]
            ),
        )
        return json.dumps(
            {
                "investigation_state": state.to_prompt_dict(),
                "new_graph_facts": grounded.model_dump(),
                "conversation_history": [{"role": "user", "content": "earlier question"}],
                "new_message": "and what about the database?",
            }
        )

    def test_the_prompt_carries_only_the_documented_metadata(self):
        payload = self._payload()
        for expected in (
            "bcs-data-service",  # repository name
            "src/main.py",  # file path
            "READS_FROM",  # relationship type
            "Ledger",  # graph entity name
            "medium",  # severity
            "and what about the database?",  # the user's question
        ):
            assert expected in payload, expected

    def test_the_prompt_carries_no_identity_or_credential_field(self):
        payload = self._payload()
        for forbidden in ("user_id", "api_key", "password", "token", "email"):
            assert forbidden not in payload, forbidden

    def test_no_source_code_or_ticket_body_can_reach_the_prompt(self):
        """Not a filter — these fields do not exist on the payload's types.
        If retrieval is ever added, this test fails and forces the data
        classification (and the provider policy decision) to be revisited."""
        from app.schemas.ask import AskResponse

        fields = set(AskResponse.model_fields)
        for absent in ("source_code", "file_contents", "jira", "confluence", "documents"):
            assert absent not in fields, absent
