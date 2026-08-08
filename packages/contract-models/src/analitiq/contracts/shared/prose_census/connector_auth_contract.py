"""Census entries for ``analitiq.contracts.connector``, part 1 of 2: the auth
models, the connection contract, resource discovery, and the function
expressions. Part 2 (:mod:`.connector_transports_document`) carries the value
expressions, transports, capability blocks, and the connector documents."""
from __future__ import annotations

from analitiq.contracts.shared.prose_obligation import (
    ENGINE_CONDUCT,
    ENGINE_OWNED_DEFAULTING,
    ProseObligation,
)

PROSE_OBLIGATIONS: tuple[ProseObligation, ...] = (
    # === connector: auth + discovery transport refs ==========================
    ProseObligation(
        model="AuthOperationTemplate", field="transport_ref",
        prose_hash="fa90d66861dd",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="PostAuthOperationRequest", field="transport_ref",
        prose_hash="fa90d66861dd",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(
        model="ResourceDiscovery", field="transport_ref",
        prose_hash="0fc255803c95",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    # === connector: auth =====================================================
    ProseObligation(
        model="AwsIamAuth",
        prose_hash="7f5588d34032",
        structural=(
            "closed StrictModel declaring no OAuth children; extra='forbid' "
            "rejects them as unknown keys"
        ),
    ),
    ProseObligation(
        model="NoneAuth",
        prose_hash="0e3ff9d8bd8f",
        structural=(
            "closed StrictModel declaring neither `authorize`, "
            "`token_exchange` nor `refresh`; extra='forbid' rejects them as "
            "unknown keys"
        ),
    ),
    ProseObligation(
        model="OAuth2AuthorizationCodeAuth",
        prose_hash="f05742268cea",
        structural=(
            "`authorize` and `token_exchange` are required (non-optional) "
            "fields"
        ),
    ),
    ProseObligation(
        model="OAuth2ClientCredentialsAuth",
        prose_hash="7ff409026db5",
        structural=(
            "`token_exchange` is a required field; `authorize` is absent from "
            "the closed model, so extra='forbid' rejects it"
        ),
    ),
    ProseObligation(
        model="CredentialsAuth",
        prose_hash="74ec207fcda3",
        waiver=(
            "authoring guidance ('use only when no narrower type fits') — a "
            "judgment between types no validator can arbitrate"
        ),
    ),
    # === connector: connection contract + discovery ==========================
    ProseObligation(
        model="ConnectionContract",
        prose_hash="57d2c52ff1d9",
        structural=(
            "the absence of a standalone `version` field is enforced by "
            "StrictModel: extra='forbid' rejects a document declaring one"
        ),
        waiver=(
            "the semver discipline it states — patch = no shape change, minor "
            "= additive, major = breaking — grades one revision of this "
            "document against another, and a single document carries only its "
            "own version, so a breaking `inputs` change under a patch bump is "
            "accepted here"
        ),
    ),
    ProseObligation(
        model="ConnectionContract", field="required_for_activation",
        prose_hash="d5dfaf2609fc",
        rule_ids=("RULE-CTOR-007",),
        waiver=(
            "reference validity is RULE-CTOR-007's; whether the paths RESOLVE "
            "before activation is engine-owned runtime state"
        ),
    ),
    ProseObligation(
        model="ConnectionContractInput", field="required", waiver=ENGINE_CONDUCT,
        prose_hash="1f94778249d6",
    ),
    ProseObligation(
        model="ConnectionContractInput", field="secret", rule_ids=("RULE-CONN-003",),
        prose_hash="b0f22af872f1",
    ),
    ProseObligation(
        model="ConnectionContractInputUI", field="options", rule_ids=("RULE-CONN-001",),
        prose_hash="39b147fbbb23",
    ),
    ProseObligation(
        model="PostAuthOutput", field="storage", rule_ids=("RULE-CTOR-002",),
        prose_hash="43a3d8c6a0bb",
    ),
    ProseObligation(
        model="PostAuthOutput", field="options_path",
        prose_hash="08d497ff5a6a",
        rule_ids=("RULE-CTOR-002",),
        waiver=(
            "the user_selection/auto_discovery gating is RULE-CTOR-002's; the "
            "response-body-root default is engine-owned at request execution"
        ),
    ),
    ProseObligation(
        model="ResourceDiscoveryImplementation", field="entrypoint",
        prose_hash="3aa9daed5d4d",
        rule_ids=("RULE-CTOR-003",),
    ),
    ProseObligation(
        model="ConnectionConditionPredicate", field="eq", waiver=ENGINE_CONDUCT,
        prose_hash="87c5d3e5ccd5",
    ),
    ProseObligation(
        model="ConnectionConditionPredicate", field="present", waiver=ENGINE_CONDUCT,
        prose_hash="e308add9896a",
    ),
    # === connector: full-census remainder (worklist order) ===================
    ProseObligation(
        model="ApiKeyAuth",
        prose_hash="6ff32f821709",
        structural=(
            "closed StrictModel declaring no credential fields — "
            "extra='forbid' rejects inline values, leaving "
            "`connection_contract.inputs` the only declaration site"
        ),
    ),
    ProseObligation(model="ApiKeyAuth", field="test", prose_hash="d60d4e461741", descriptive=True),
    ProseObligation(model="ApiKeyAuth", field="type", prose_hash="8ff1f9a47f14", descriptive=True),
    ProseObligation(
        model="AuthOperationTemplate",
        prose_hash="4f4b7643815d",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(model="AuthOperationTemplate", field="body", prose_hash="a267b5b40716", descriptive=True),
    ProseObligation(model="AuthOperationTemplate", field="headers", prose_hash="5ee760ce7f16", descriptive=True),
    ProseObligation(model="AuthOperationTemplate", field="headers_remove", prose_hash="6588e039a44e", descriptive=True),
    ProseObligation(
        model="AuthOperationTemplate", field="method",
        prose_hash="55f1c7934b0f",
        waiver=(
            "the stated absence default is engine-substituted at dispatch "
            "time (the document records only the field's absence); the "
            "declare-`method`-explicitly clause for non-HTTP transports is "
            "authoring advice enforced by nothing — no offline check requires "
            "`method` when the resolved transport family is not HTTP"
        ),
    ),
    ProseObligation(model="AuthOperationTemplate", field="path", prose_hash="ca5a36fd82a1", descriptive=True),
    ProseObligation(model="AwsIamAuth", field="test", prose_hash="d60d4e461741", descriptive=True),
    ProseObligation(model="AwsIamAuth", field="type", prose_hash="8ff1f9a47f14", descriptive=True),
    ProseObligation(model="Base64EncodeDerived", prose_hash="4a4608e799e7", descriptive=True),
    ProseObligation(model="Base64EncodeDerived", field="function", prose_hash="220b5ed727c9", descriptive=True),
    ProseObligation(model="Base64EncodeDerived", field="input", prose_hash="cb4c593f5a8c", descriptive=True),
    ProseObligation(
        model="BasicAuth",
        prose_hash="c22c2830e108",
        structural=(
            "closed StrictModel declaring no credential fields — "
            "extra='forbid' rejects field names declared here, leaving "
            "`connection_contract.inputs` the only declaration site"
        ),
    ),
    ProseObligation(model="BasicAuth", field="test", prose_hash="d60d4e461741", descriptive=True),
    ProseObligation(model="BasicAuth", field="type", prose_hash="8ff1f9a47f14", descriptive=True),
    ProseObligation(model="BasicAuthDerived", prose_hash="5a8f9ad3982b", descriptive=True),
    ProseObligation(model="BasicAuthDerived", field="function", prose_hash="220b5ed727c9", descriptive=True),
    ProseObligation(model="BasicAuthDerived", field="input", prose_hash="0069b1e23659", descriptive=True),
    ProseObligation(model="BasicAuthDerivedInput", prose_hash="ad835a39363d", descriptive=True),
    ProseObligation(model="BasicAuthDerivedInput", field="password", prose_hash="f8edca37d48c", descriptive=True),
    ProseObligation(model="BasicAuthDerivedInput", field="username", prose_hash="800111632685", descriptive=True),
    ProseObligation(
        model="ConnectionConditionPredicate", rule_ids=("RULE-CTOR-012",),
        prose_hash="bc558a07976a",
    ),
    ProseObligation(
        model="ConnectionConditionPredicate", field="field",
        prose_hash="af6ae1c32ae9",
        rule_ids=("RULE-CTOR-008",),
    ),
    ProseObligation(
        model="ConnectionConditionPredicate", field="in_",
        prose_hash="93976bd17701",
        structural="a Field length floor plus the ConditionScalar item type",
    ),
    ProseObligation(
        model="ConnectionConditionPredicate", field="not_in",
        prose_hash="9621af74abc2",
        structural="a Field length floor plus the ConditionScalar item type",
    ),
    ProseObligation(
        model="ConnectionConditionPredicate", field="regex",
        prose_hash="fd8a64f71353",
        waiver=(
            "the engine applies the value as a regular expression when it "
            "evaluates the predicate; that the string compiles as one is "
            "unenforced offline"
        ),
    ),
    ProseObligation(model="ConnectionContract", field="inputs", prose_hash="b6fb94aaaec0", descriptive=True),
    ProseObligation(model="ConnectionContract", field="post_auth_outputs", prose_hash="86965fce6150", descriptive=True),
    ProseObligation(
        model="ConnectionContract", field="validation",
        prose_hash="41849d789a84",
        waiver=(
            "authoring placement judgment: whether a constraint belongs on a "
            "single input or in a cross-input rule is not statically decidable "
            "from the document"
        ),
    ),
    ProseObligation(model="ConnectionContractInput", prose_hash="456973dcc40e", descriptive=True),
    ProseObligation(model="ConnectionContractInput", field="default", prose_hash="28c423268631", descriptive=True),
    ProseObligation(
        model="ConnectionContractInput", field="enum",
        prose_hash="3737ab7e0469",
        structural=(
            "non-emptiness when present is enforced by the single-field check "
            "on `enum` inside `_consistency`"
        ),
    ),
    ProseObligation(model="ConnectionContractInput", field="format", prose_hash="1f92b1ae88ca", descriptive=True),
    ProseObligation(
        model="ConnectionContractInput", field="pattern",
        prose_hash="8b64ebc84262",
        waiver=(
            "the engine applies the value as a regular expression when "
            "validating submitted string inputs; that the string compiles as "
            "one is unenforced offline"
        ),
    ),
    ProseObligation(
        model="ConnectionContractInput", field="phase",
        prose_hash="3ecaa2a04318",
        structural="the `InputPhase` enum type closes the value set",
    ),
    ProseObligation(
        model="ConnectionContractInput", field="source",
        prose_hash="f87a850eb030",
        structural="the `InputSource` enum type closes the value set",
    ),
    ProseObligation(
        model="ConnectionContractInput", field="storage",
        prose_hash="04cc21d79dcc",
        structural="the `ContractInputStorage` Literal alias closes the value set",
    ),
    ProseObligation(
        model="ConnectionContractInput", field="type",
        prose_hash="a958a7856253",
        structural="a Literal type closes the vocabulary",
    ),
    ProseObligation(model="ConnectionContractInput", field="ui", prose_hash="a40f978f2f38", descriptive=True),
    ProseObligation(model="ConnectionContractInputUI", prose_hash="55f2016815ef", descriptive=True),
    ProseObligation(model="ConnectionContractInputUI", field="default", prose_hash="f38e7c63167b", descriptive=True),
    ProseObligation(model="ConnectionContractInputUI", field="help_text", prose_hash="c9be279a1770", descriptive=True),
    ProseObligation(model="ConnectionContractInputUI", field="label", prose_hash="55785e933aa6", descriptive=True),
    ProseObligation(model="ConnectionContractInputUI", field="placeholder", prose_hash="e929661d00b6", descriptive=True),
    ProseObligation(model="ConnectionContractInputUI", field="widget", prose_hash="7a3b208e3ef3", descriptive=True),
    ProseObligation(
        model="InputPhase",
        prose_hash="f676d0310d71",
        structural=(
            "the `InputPhase` `Enum`'s own closed membership — it declares no "
            "post-auth member, so `inputs` cannot claim a post-auth `phase`"
        ),
        waiver=(
            "whether a value is genuinely produced only after authentication "
            "is authoring judgment about the provider; the closed membership "
            "rejects a literal post-auth `phase`, not a value misfiled in "
            "`inputs` instead of `post_auth_outputs`"
        ),
    ),
    ProseObligation(
        model="InputSource",
        prose_hash="06e5c3a50b08",
        structural=(
            "the `InputSource` `Enum`'s own closed membership — it declares no "
            "post-auth member, so `inputs` cannot claim a post-auth `source`"
        ),
        waiver=(
            "whether a value is genuinely produced only after authentication "
            "is authoring judgment about the provider; the closed membership "
            "rejects a literal post-auth `source`, not a value misfiled in "
            "`inputs` instead of `post_auth_outputs`"
        ),
    ),
    ProseObligation(model="PostAuthOutputMode", prose_hash="52af6dd1ce37", descriptive=True),
    ProseObligation(model="ConnectionContractValidation", prose_hash="776265d26114", descriptive=True),
    ProseObligation(
        model="ConnectionContractValidation", field="rules",
        prose_hash="cdfc89b6a2ce",
        waiver=(
            "authoring placement judgment: whether a constraint belongs on a "
            "single input or in a cross-input rule is not statically decidable "
            "from the document"
        ),
    ),
    ProseObligation(model="ConnectionContractValidationRule", prose_hash="b329ae3c0466", descriptive=True),
    ProseObligation(
        model="ConnectionContractValidationRule", field="forbid", waiver=ENGINE_CONDUCT,
        prose_hash="8746085a3aeb",
    ),
    ProseObligation(model="ConnectionContractValidationRule", field="message", prose_hash="97cbebe4f839", descriptive=True),
    ProseObligation(
        model="ConnectionContractValidationRule", field="require", waiver=ENGINE_CONDUCT,
        prose_hash="39ffc2a1235d",
    ),
    ProseObligation(model="ConnectionContractValidationRule", field="when", prose_hash="ca26ad700a42", descriptive=True),
    ProseObligation(model="CredentialsAuth", field="test", prose_hash="d60d4e461741", descriptive=True),
    ProseObligation(model="CredentialsAuth", field="type", prose_hash="8ff1f9a47f14", descriptive=True),
    ProseObligation(
        model="DbAuth",
        prose_hash="2cbc556b7f7d",
        structural=(
            "closed StrictModel declaring no credential fields — "
            "extra='forbid' rejects inline values, leaving "
            "`connection_contract.inputs` the only declaration site"
        ),
    ),
    ProseObligation(model="DbAuth", field="test", prose_hash="d60d4e461741", descriptive=True),
    ProseObligation(model="DbAuth", field="type", prose_hash="8ff1f9a47f14", descriptive=True),
    ProseObligation(model="FormFieldOption", prose_hash="c5761ddcab71", descriptive=True),
    ProseObligation(model="FormFieldOption", field="label", prose_hash="a5c8b5305150", descriptive=True),
    ProseObligation(model="FormFieldOption", field="value", prose_hash="d9c274c41830", descriptive=True),
    ProseObligation(
        model="JwtAuth",
        prose_hash="5a58e77d9f3f",
        structural=(
            "closed StrictModel declaring no signing fields — extra='forbid' "
            "rejects inline values, leaving `connection_contract.inputs` the "
            "only declaration site"
        ),
    ),
    ProseObligation(model="JwtAuth", field="test", prose_hash="d60d4e461741", descriptive=True),
    ProseObligation(model="JwtAuth", field="type", prose_hash="8ff1f9a47f14", descriptive=True),
    ProseObligation(model="LookupDerived", prose_hash="f4b462b3a1b2", descriptive=True),
    ProseObligation(model="LookupDerived", field="function", prose_hash="220b5ed727c9", descriptive=True),
    ProseObligation(model="LookupDerived", field="input", prose_hash="650df15f7458", descriptive=True),
    ProseObligation(model="LookupDerived", field="map", prose_hash="fe9290c54dff", descriptive=True),
    ProseObligation(model="NoneAuth", field="test", prose_hash="d60d4e461741", descriptive=True),
    ProseObligation(model="NoneAuth", field="type", prose_hash="8ff1f9a47f14", descriptive=True),
    ProseObligation(model="OAuth2AuthorizationCodeAuth", field="authorize", prose_hash="3a75ab8db254", descriptive=True),
    ProseObligation(model="OAuth2AuthorizationCodeAuth", field="refresh", prose_hash="aa3efd6f0c48", descriptive=True),
    ProseObligation(model="OAuth2AuthorizationCodeAuth", field="test", prose_hash="d60d4e461741", descriptive=True),
    ProseObligation(model="OAuth2AuthorizationCodeAuth", field="token_exchange", prose_hash="4cea623388a8", descriptive=True),
    ProseObligation(model="OAuth2AuthorizationCodeAuth", field="type", prose_hash="8ff1f9a47f14", descriptive=True),
    ProseObligation(model="OAuth2ClientCredentialsAuth", field="refresh", prose_hash="aa3efd6f0c48", descriptive=True),
    ProseObligation(model="OAuth2ClientCredentialsAuth", field="test", prose_hash="d60d4e461741", descriptive=True),
    ProseObligation(model="OAuth2ClientCredentialsAuth", field="token_exchange", prose_hash="4cea623388a8", descriptive=True),
    ProseObligation(model="OAuth2ClientCredentialsAuth", field="type", prose_hash="8ff1f9a47f14", descriptive=True),
    ProseObligation(model="PostAuthOperationRequest", prose_hash="dec0035f939a", descriptive=True),
    ProseObligation(model="PostAuthOperationRequest", field="body", prose_hash="a267b5b40716", descriptive=True),
    ProseObligation(model="PostAuthOperationRequest", field="headers", prose_hash="5ee760ce7f16", descriptive=True),
    ProseObligation(
        model="PostAuthOperationRequest", field="method",
        prose_hash="4a46190b8ec6",
        waiver=(
            "the stated absence default is engine-substituted at dispatch "
            "time (the document records only the field's absence); the "
            "declare-`method`-explicitly clause for non-HTTP transports is "
            "authoring advice enforced by nothing — no offline check requires "
            "`method` when the resolved transport family is not HTTP"
        ),
    ),
    ProseObligation(model="PostAuthOperationRequest", field="path", prose_hash="ca5a36fd82a1", descriptive=True),
    ProseObligation(
        model="PostAuthOutput",
        prose_hash="f938ece5a028",
        structural=(
            "extra='forbid' on the closed StrictModel rejects `source`, "
            "`phase`, `required` and `secret` like any undeclared key"
        ),
    ),
    ProseObligation(
        model="PostAuthOutput", field="discovery_request", rule_ids=("RULE-CTOR-002",),
        prose_hash="3a626720c1d7",
    ),
    ProseObligation(model="PostAuthOutput", field="format", prose_hash="1ec26cc4d171", descriptive=True),
    ProseObligation(
        model="PostAuthOutput", field="label_path", rule_ids=("RULE-CTOR-002",),
        prose_hash="115720589be7",
    ),
    ProseObligation(
        model="PostAuthOutput", field="mode",
        prose_hash="4fdaf680c1fc",
        structural="the `PostAuthOutputMode` enum type closes the value set",
    ),
    ProseObligation(
        model="PostAuthOutput", field="options_request", rule_ids=("RULE-CTOR-002",),
        prose_hash="faf2df8e26a8",
    ),
    ProseObligation(
        model="PostAuthOutput", field="type",
        prose_hash="0e776f629a40",
        structural="a Literal type closes the vocabulary",
    ),
    ProseObligation(model="PostAuthOutput", field="ui", prose_hash="a40f978f2f38", descriptive=True),
    ProseObligation(model="PostAuthOutput", field="value_path", prose_hash="957c1eaa810e", descriptive=True),
    ProseObligation(model="ResourceDiscovery", prose_hash="018487df01f8", descriptive=True),
    ProseObligation(
        model="ResourceDiscovery", field="implementation",
        prose_hash="69db520ebf65",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
    ProseObligation(model="ResourceDiscovery", field="options", prose_hash="5f410f795498", descriptive=True),
    ProseObligation(model="ResourceDiscovery", field="produces", prose_hash="aed6cf3705c8", descriptive=True),
    ProseObligation(
        model="ResourceDiscovery", field="strategy",
        prose_hash="83bdf2493f6a",
        waiver=(
            "the discovery-strategy registry is engine-owned at configure "
            "time; the contract carries no catalog to resolve the ID against"
        ),
    ),
    ProseObligation(model="ResourceDiscovery", field="triggers", prose_hash="60e1ac562bee", descriptive=True),
    ProseObligation(model="ResourceDiscoveryImplementation", prose_hash="2d54f156ad80", descriptive=True),
    ProseObligation(model="ResourceDiscoveryImplementation", field="type", prose_hash="37bdbb430b3e", descriptive=True),
    ProseObligation(model="ResourceDiscoveryTriggers", prose_hash="6eae23d08bce", descriptive=True),
    ProseObligation(model="ResourceDiscoveryTriggers", field="describe_resource", prose_hash="71b18cf170dd", descriptive=True),
    ProseObligation(model="ResourceDiscoveryTriggers", field="list_resources", prose_hash="02757f4e0d63", descriptive=True),
    ProseObligation(model="UrlEncodeDerived", prose_hash="a320f8952d72", descriptive=True),
    ProseObligation(model="UrlEncodeDerived", field="function", prose_hash="220b5ed727c9", descriptive=True),
    ProseObligation(model="UrlEncodeDerived", field="input", prose_hash="cb4c593f5a8c", descriptive=True),
    ProseObligation(
        model="UrlEncodeDerived", field="safe",
        prose_hash="67fc8d7b92f2",
        waiver=ENGINE_OWNED_DEFAULTING,
    ),
)
