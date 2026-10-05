"""Schema and semantic conformance vectors for host adapter contract v1."""

import copy
import json
import re
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource


ROOT = Path(__file__).parents[1]
SCHEMAS = ROOT / "governance/product/host-adapter/v1/schemas"
FIXTURES = ROOT / "governance/product/host-adapter/v1/fixtures"
CHECKER = FormatChecker()
CONFIG_KEYWORDS = {
    "type", "properties", "required", "additionalProperties", "items", "enum", "const",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
    "minItems", "maxItems", "minProperties", "maxProperties", "title", "description",
}
CONFIG_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
CONFIG_BOUND_TYPES = {
    "minimum": {"integer", "number"}, "maximum": {"integer", "number"},
    "exclusiveMinimum": {"integer", "number"}, "exclusiveMaximum": {"integer", "number"},
    "minLength": {"string"}, "maxLength": {"string"}, "minItems": {"array"}, "maxItems": {"array"},
    "minProperties": {"object"}, "maxProperties": {"object"},
}


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path):
    return json.loads(path.read_text(), object_pairs_hook=_pairs_no_duplicates,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def parse_bounded_json(raw):
    """Parse UTF-8 JSON only after applying its byte limit to the raw input."""
    if len(raw) > 65536:
        raise ValueError("configuration JSON exceeds 64 KiB")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    configuration_limits(value)
    return value


def validate_config_profile(node, *, root=False, depth=0, budget=None):
    """Check the published bounded schema profile before using Draft 2020-12."""
    if budget is None:
        budget = [0]
    if root:
        configuration_limits(node)
    budget[0] += 1
    if budget[0] > 4096 or depth > 16 or not isinstance(node, dict):
        raise ValueError("configuration schema exceeds profile limits or is not an object")
    allowed = CONFIG_KEYWORDS | ({"$schema"} if root else set())
    if set(node) - allowed:
        raise ValueError("unsupported configuration schema keyword")
    if root and node.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError("unsupported configuration schema dialect")
    if node.get("type") not in CONFIG_TYPES or (root and node["type"] != "object"):
        raise ValueError("invalid configuration schema type")
    kind = node["type"]
    for key, kinds in CONFIG_BOUND_TYPES.items():
        if key in node and kind not in kinds:
            raise ValueError(f"{key} does not apply to {kind}")
    for low, high in (("minimum", "maximum"), ("exclusiveMinimum", "exclusiveMaximum"), ("minLength", "maxLength"), ("minItems", "maxItems"), ("minProperties", "maxProperties")):
        if low in node and high in node and node[low] > node[high]:
            raise ValueError(f"inconsistent {low}/{high} bounds")
    if kind in {"integer", "number"}:
        lower = [(node[key], key == "minimum") for key in ("minimum", "exclusiveMinimum") if key in node]
        upper = [(node[key], key == "maximum") for key in ("maximum", "exclusiveMaximum") if key in node]
        if lower and upper:
            lower_value = max(value for value, _ in lower)
            upper_value = min(value for value, _ in upper)
            lower_inclusive = all(inclusive for value, inclusive in lower if value == lower_value)
            upper_inclusive = all(inclusive for value, inclusive in upper if value == upper_value)
            if lower_value > upper_value or (lower_value == upper_value and not (lower_inclusive and upper_inclusive)):
                raise ValueError("inconsistent numeric bounds admit no value")
    if kind == "object":
        if "additionalProperties" not in node:
            raise ValueError("object schema must specify additionalProperties")
        props = node.get("properties", {})
        if not isinstance(props, dict):
            raise ValueError("properties must be an object")
        for child in props.values():
            validate_config_profile(child, depth=depth + 1, budget=budget)
        additional = node["additionalProperties"]
        if isinstance(additional, dict):
            validate_config_profile(additional, depth=depth + 1, budget=budget)
        elif not isinstance(additional, bool):
            raise ValueError("additionalProperties must be boolean or schema")
        required = node.get("required", [])
        if not isinstance(required, list) or any(not isinstance(v, str) for v in required) or len(set(required)) != len(required):
            raise ValueError("required must be unique strings")
    elif "properties" in node or "required" in node or "additionalProperties" in node:
        raise ValueError("object-only keyword used on non-object schema")
    if kind == "array":
        if "items" not in node:
            raise ValueError("array schema must specify items")
        validate_config_profile(node["items"], depth=depth + 1, budget=budget)
    elif "items" in node:
        raise ValueError("items used on non-array schema")
    if "enum" in node:
        enum = node["enum"]
        if not isinstance(enum, list) or not 1 <= len(enum) <= 64 or len({json.dumps(v, sort_keys=True) for v in enum}) != len(enum) or any(isinstance(v, (dict, list)) for v in enum):
            raise ValueError("enum must contain 1..64 unique JSON scalars")
    if "const" in node and isinstance(node["const"], (dict, list)):
        raise ValueError("const must be a JSON scalar")
    for key in ("title", "description"):
        if key in node and (not isinstance(node[key], str) or len(node[key]) > 1024):
            raise ValueError(f"invalid {key}")
    Draft202012Validator.check_schema(node)


def configuration_limits(value):
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(encoded) > 65536:
        raise ValueError("configuration JSON exceeds 64 KiB")
    def walk(item, depth=0):
        if depth > 16:
            raise ValueError("configuration JSON exceeds depth 16")
        if isinstance(item, dict):
            return 1 + sum(walk(k, depth + 1) + walk(v, depth + 1) for k, v in item.items())
        if isinstance(item, list):
            return 1 + sum(walk(v, depth + 1) for v in item)
        return 1
    if walk(value) > 4096:
        raise ValueError("configuration JSON exceeds 4096 nodes")


def semver_parts(value):
    match = SEMVER.fullmatch(value)
    if not match:
        raise ValueError("invalid SemVer")
    major, minor, patch, prerelease, _build = match.groups()
    ids = prerelease.split(".") if prerelease else None
    return int(major), int(minor), int(patch), ids


def semver_compare(left, right):
    a, b = semver_parts(left), semver_parts(right)
    if a[:3] != b[:3]:
        return (a[:3] > b[:3]) - (a[:3] < b[:3])
    ap, bp = a[3], b[3]
    if ap is None or bp is None:
        return 0 if ap is bp else (1 if ap is None else -1)
    for x, y in zip(ap, bp):
        if x == y:
            continue
        xn, yn = x.isdigit(), y.isdigit()
        if xn and yn:
            return (int(x) > int(y)) - (int(x) < int(y))
        if xn != yn:
            return -1 if xn else 1
        return (x > y) - (x < y)
    return (len(ap) > len(bp)) - (len(ap) < len(bp))


def validate_manifest_semantics(manifest):
    providers = [entry["provider"] for entry in manifest["host_version_constraints"]]
    if len(providers) != len(set(providers)):
        raise ValueError("duplicate host provider constraint")
    for entry in manifest["host_version_constraints"]:
        for bound in (entry["minimum"], entry["maximum"]):
            if bound is not None:
                semver_parts(bound)
        if entry["minimum"] is not None and entry["maximum"] is not None and semver_compare(entry["minimum"], entry["maximum"]) > 0:
            raise ValueError("reversed host version range")
    versions = manifest["contract_version_range"]
    if versions["minimum"] > versions["maximum"]:
        raise ValueError("reversed host contract range")
    validate_config_profile(manifest["configuration_schema"], root=True)


def compatibility(manifest, supported_protocols=(1,), supported_contracts=(1,)):
    protocol = sorted(set(manifest["protocol_versions"]) & set(supported_protocols))
    contract = [v for v in supported_contracts if manifest["contract_version_range"]["minimum"] <= v <= manifest["contract_version_range"]["maximum"]]
    if not protocol:
        return None, "protocol_version_incompatible"
    if not contract:
        return None, "host_contract_version_incompatible"
    return (max(protocol), max(contract)), None


def matches_pending_response(request, response):
    if request["request_id"] != response.get("request_id"):
        return False
    if request["operation"] == "handshake":
        if any(key in response for key in ("protocol_version", "host_contract_version", "result")):
            return False
        if "selected_version" in response:
            return "error" not in response and response["selected_version"] in request["offered_versions"]
        return isinstance(response.get("error"), dict)
    return (
        request["protocol_version"] == response.get("protocol_version")
        and request["host_contract_version"] == response.get("host_contract_version")
        and "selected_version" not in response
        and (("result" in response) != ("error" in response))
    )


def effective_capability_state(lifecycle, observations):
    """Reference state precedence; observations are contextual runtime evidence."""
    reasons = []
    admin = observations.get("admin_prohibition")
    trust_needed = observations.get("trust_required", False)
    unavailable = observations.get("unavailable")
    prerequisites = observations.get("unresolved_prerequisites", [])
    for key, value in (("admin", admin), ("trust", trust_needed), ("unavailable", unavailable)):
        if value:
            reasons.append(value.get("reason", key) if isinstance(value, dict) else key)
    reasons.extend(prerequisites)
    disabled = lifecycle.get("enabled") is False
    if disabled:
        reasons.append("disabled")
    if admin and admin.get("verified"):
        return "admin-disabled", reasons
    if trust_needed and (lifecycle["adapter_trust"] != "trusted" or lifecycle["host_trust"] != "trusted"):
        return "trust-required", reasons
    if unavailable and unavailable.get("verified"):
        return "unavailable", reasons
    if disabled:
        return "unavailable", reasons
    if prerequisites:
        return "unknown", reasons
    if lifecycle["adapter_trust"] != "trusted" or lifecycle["host_trust"] != "trusted":
        return "unknown", reasons
    candidates = observations.get("support_evidence", [])
    usable = [item for item in candidates if item.get("verified") and item.get("fresh") and item.get("context_match") and not item.get("drifted_fields")]
    kinds = {item["claim"] for item in usable}
    if len(kinds) != 1:
        return "unknown", reasons
    claim = kinds.pop()
    if claim not in {"supported", "limited"}:
        return "unknown", reasons
    return claim, reasons


class HostAdapterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schemas = {p.name.removesuffix(".schema.json"): load(p) for p in SCHEMAS.glob("*.schema.json")}
        cls.registry = Registry()
        for schema in cls.schemas.values():
            Draft202012Validator.check_schema(schema)
            if "$id" in schema:
                cls.registry = cls.registry.with_resource(schema["$id"], Resource.from_contents(schema))

    def validator(self, name):
        return Draft202012Validator(self.schemas[name], registry=self.registry, format_checker=CHECKER)

    def protocol_validator(self, part):
        schema = self.schemas["protocol-message"]
        return Draft202012Validator({"$ref": f"#/$defs/{part}", "$defs": schema["$defs"]}, registry=self.registry, format_checker=CHECKER)

    def pending_response_matches(self, request, response):
        """Validate envelope correlation and the result type selected by request."""
        try:
            self.protocol_validator("response").validate(response)
        except ValidationError:
            return False
        if not matches_pending_response(request, response):
            return False
        if "result" not in response:
            return True
        schema_id = self.schemas["protocol-message"]["$id"]
        result_validator = Draft202012Validator(
            {"$ref": f"{schema_id}#/$defs/results/{request['operation']}"},
            registry=self.registry,
            format_checker=CHECKER,
        )
        return not list(result_validator.iter_errors(response["result"]))

    def assert_invalid(self, validator, value):
        self.assertTrue(list(validator.iter_errors(value)), json.dumps(value, sort_keys=True))

    def test_authored_vectors_and_open_extensions(self):
        for fixture, schema in {
            "valid-snapshot-codex-shape-only-unknown.json": "host-capability-snapshot",
            "valid-snapshot-claude-trust-required.json": "host-capability-snapshot",
            "valid-snapshot-admin-disabled.json": "host-capability-snapshot",
            "valid-event-codex-tool-before.json": "canonical-host-event",
            "valid-event-record-only.json": "canonical-host-event",
            "valid-event-synthetic-usage.json": "canonical-host-event",
            "valid-manifest-synthetic.json": "executable-adapter-manifest",
            "valid-cli-envelope.json": "cli-operation-envelope",
        }.items():
            value = load(FIXTURES / fixture)
            self.validator(schema).validate(value)
            if schema == "executable-adapter-manifest":
                validate_manifest_semantics(value)
        for fixture, schema, part in (
            ("invalid-extra-manifest-execution-field.json", "executable-adapter-manifest", None),
            ("invalid-unsupported-protocol-version.json", "protocol-message", "request"),
            ("invalid-malformed-event.json", "canonical-host-event", None),
            ("invalid-event-absent-identity.json", "canonical-host-event", None),
        ):
            validator = self.protocol_validator(part) if part else self.validator(schema)
            value = load(FIXTURES / fixture)
            if fixture == "invalid-extra-manifest-execution-field.json":
                valid_base = copy.deepcopy(value)
                del valid_base["post_install_command"]
                self.validator(schema).validate(valid_base)
                validate_manifest_semantics(valid_base)
            elif fixture == "invalid-unsupported-protocol-version.json":
                valid_base = copy.deepcopy(value)
                valid_base["protocol_version"] = 1
                self.protocol_validator(part).validate(valid_base)
            self.assert_invalid(validator, value)
        protocol = load(FIXTURES / "valid-protocol.json")
        for key, part in (("request", "request"), ("handshake_response", "response"), ("unsupported_version_response", "response"), ("operation_request", "request"), ("operation_response", "response"), ("operation_error", "response")):
            self.protocol_validator(part).validate(protocol[key])
        snapshot = load(FIXTURES / "valid-snapshot-admin-disabled.json")
        event = load(FIXTURES / "valid-event-record-only.json")
        inputs = {
            "probe": {"context": {"scope": "session"}},
            "normalize": {"native_payload": {}, "source": event["source"], "host_id": "host-synthetic", "observed_at": "2026-10-05T00:00:00Z", "capability_snapshot": snapshot},
            "encode_control": {"intent": {"schema_version": 1, "request_id": "r", "event_id": event["event_id"], "product_id": "circinus", "decision_ref": "decision-1", "action": "deny", "required_capability_keys": ["tool.pre.deny"], "required_failure_class": "circinus.coverage_missing"}, "event": event, "capability_snapshot": snapshot},
            "plan_config": {"product_id": "circinus", "validator_ref": {"id": "circinus-config", "version": 1, "digest": "sha256:" + "c" * 64}, "context": {"scope": "session"}, "request": {}},
        }
        results = {
            "probe": {"snapshot": snapshot},
            "normalize": {"events": [event]},
            "encode_control": {"schema_version": 1, "status": "unsupported", "event_id": event["event_id"], "action": "deny", "reason_code": "capability_unavailable", "delivery": "not_attempted"},
            "plan_config": {"product_id": "circinus", "validator_ref": inputs["plan_config"]["validator_ref"], "plan": {}},
        }
        schema_id = self.schemas["protocol-message"]["$id"]
        for operation in inputs:
            with self.subTest(operation=operation):
                input_validator = Draft202012Validator({"$ref": f"{schema_id}#/$defs/inputs/{operation}"}, registry=self.registry, format_checker=CHECKER)
                result_validator = Draft202012Validator({"$ref": f"{schema_id}#/$defs/results/{operation}"}, registry=self.registry, format_checker=CHECKER)
                input_validator.validate(inputs[operation])
                result_validator.validate(results[operation])

    def test_tool_fact_completeness_and_record_only_boundaries(self):
        validator = self.validator("canonical-host-event")
        base = load(FIXTURES / "valid-event-record-only.json")
        validator.validate(base)
        self.assertNotIn("action_kind", base["facts"])
        for badfacts in (
            {}, {"tool_name": ""},
            {"tool_name": "Bash", "action_kind": "shell_command", "operand_completeness": "complete", "operands_availability": "not_provided", "operands": {"command": "echo hi"}},
            {"tool_name": "Bash", "action_kind": "shell_command", "operand_completeness": "complete", "operands_availability": "observed", "operands": {"command": ""}},
            {"tool_name": "Bash", "action_kind": "file_mutation", "operand_completeness": "complete", "operands_availability": "observed", "operands": {"files": []}},
            {"tool_name": "Bash", "action_kind": "file_mutation", "operand_completeness": "complete", "operands_availability": "observed", "operands": {"files": [{"operation": "move", "path": "a"}]}},
            {"tool_name": "Bash", "action_kind": "file_mutation", "operand_completeness": "complete", "operands_availability": "observed", "operands": {"files": [{"operation": "update", "path": "a", "destination_path": "b"}]}},
            {"tool_name": "Bash", "action_kind": "unknown", "operand_completeness": "complete", "operands_availability": "observed", "operands": {"command": "echo"}},
        ):
            event = copy.deepcopy(base)
            event["facts"] = badfacts
            self.assert_invalid(validator, event)
        event = copy.deepcopy(base)
        event["facts"] = {"tool_name": "Bash", "action_kind": "shell_command", "operand_completeness": "complete", "operands_availability": "observed", "operands": {"command": "echo hi"}}
        validator.validate(event)
        event["facts"] = {"tool_name": "Edit", "action_kind": "file_mutation", "operand_completeness": "complete", "operands_availability": "observed", "operands": {"files": [{"operation": "move", "path": "a", "destination_path": "b"}, {"operation": "update", "path": "c"}]}}
        validator.validate(event)
        event["facts"] = {"tool_name": "Bash", "action_kind": "shell_command", "operand_completeness": "partial", "operands_availability": "unavailable"}
        validator.validate(event)
        event["facts"] = {"tool_name": "Edit", "action_kind": "file_mutation", "operand_completeness": "unknown"}
        validator.validate(event)
        child = copy.deepcopy(base)
        child["identity"].update(lineage_status="child", parent_agent_id="")
        self.assert_invalid(validator, child)
        child["identity"]["parent_agent_id"] = "parent"
        child["identity"]["tool_provider"] = "new_provider"
        child["identity"]["future_identity_field"] = "opaque"
        child["identity"].update(tool_instance_id="native-tool-1", provider_session_id="native-session-1", event_id="native-event-1")
        validator.validate(child)
        self.assertEqual(child["identity"]["tool_instance_id"], "native-tool-1")
        unknown = copy.deepcopy(base)
        unknown["identity"].update(lineage_status="unknown", parent_agent_id="parent")
        self.assert_invalid(validator, unknown)
        root = copy.deepcopy(base)
        root["identity"].update(lineage_status="root", parent_agent_id="parent")
        self.assert_invalid(validator, root)

    def test_protocol_root_branch_exclusivity_and_typed_operations(self):
        root = self.validator("protocol-message")
        self.assert_invalid(root, {})
        good_error = {"protocol": "horonom.host-adapter", "request_id": "r", "protocol_version": 1, "host_contract_version": 1, "error": {"code": "unsupported_version", "message": "x"}}
        root.validate(good_error)
        for bad in (
            {"protocol": "horonom.host-adapter", "request_id": "r", "selected_version": 1, "error": {"code": "failed", "message": "x"}},
            {"protocol": "horonom.host-adapter", "request_id": "r", "selected_version": 1, "protocol_version": 2},
            {"protocol": "horonom.host-adapter", "request_id": "r", "selected_version": 1, "result": {}, "error": {"code": "failed", "message": "x"}},
            {"protocol": "horonom.host-adapter", "request_id": "r", "protocol_version": 1, "host_contract_version": 1, "result": {}, "error": {"code": "failed", "message": "x"}},
        ):
            self.assert_invalid(root, bad)
        defs = self.schemas["protocol-message"]["$defs"]
        for operation, invalid_input in (("probe", {}), ("normalize", {}), ("encode_control", {"intent": {"action": "allow_anyway"}, "event": {}, "capability_snapshot": {}}), ("plan_config", {"product_id": "p", "validator_ref": {}, "context": {}, "request": {}})):
            with self.subTest(operation=operation):
                operation_schema = {"$ref": self.schemas["protocol-message"]["$id"] + f"#/$defs/inputs/{operation}"}
                self.assert_invalid(Draft202012Validator(operation_schema, registry=self.registry), invalid_input)
        valid_intent = {"schema_version": 1, "request_id": "r", "event_id": "e", "product_id": "circinus", "decision_ref": "decision-1", "action": "allow_anyway", "required_capability_keys": ["tool.pre.allow"], "required_failure_class": "circinus.coverage_missing"}
        self.assert_invalid(Draft202012Validator({"$ref": self.schemas["protocol-message"]["$id"] + "#/$defs/controlIntent"}, registry=self.registry), valid_intent)
        intent = {"schema_version": 1, "request_id": "r", "event_id": "e", "product_id": "circinus", "decision_ref": "decision-1", "action": "rewrite", "required_capability_keys": ["tool.pre.rewrite"], "required_failure_class": "circinus.coverage_missing", "rewrite_input": {"command": "echo ok"}}
        intent_schema = {"$ref": self.schemas["protocol-message"]["$id"] + "#/$defs/controlIntent"}
        Draft202012Validator(intent_schema, registry=self.registry).validate(intent)
        del intent["rewrite_input"]
        self.assert_invalid(Draft202012Validator(intent_schema, registry=self.registry), intent)
        protocol = load(FIXTURES / "valid-protocol.json")
        response = protocol["operation_response"]
        self.assertEqual(response["request_id"], "req-2")
        self.assertEqual(response["host_contract_version"], 1)
        self.assertTrue(self.pending_response_matches(protocol["operation_request"], response))
        self.assertFalse(self.pending_response_matches(protocol["operation_request"], {**response, "request_id": "wrong"}))
        self.assertFalse(self.pending_response_matches(protocol["operation_request"], {**response, "host_contract_version": 2}))
        self.assertFalse(self.pending_response_matches(protocol["operation_request"], protocol["request"]))
        self.assertFalse(self.pending_response_matches(protocol["operation_request"], protocol["unsupported_version_response"]))
        self.assertFalse(self.pending_response_matches(
            protocol["operation_request"],
            {**response, "result": {"events": []}},
        ))
        self.assertTrue(self.pending_response_matches(
            protocol["operation_request"],
            {**response, "trace_hint": "ignored extension"},
        ))
        self.assertFalse(self.pending_response_matches(protocol["request"], response))
        self.assertFalse(self.pending_response_matches(protocol["request"], protocol["operation_response"]))
        self.assertTrue(self.pending_response_matches(protocol["request"], protocol["unsupported_version_response"]))
        self.assertFalse(self.pending_response_matches(
            protocol["request"], {**protocol["unsupported_version_response"], "request_id": "wrong"}
        ))
        self.assertTrue(matches_pending_response(protocol["request"], protocol["handshake_response"]))
        self.assertTrue(matches_pending_response(protocol["request"], protocol["unsupported_version_response"]))
        self.assertFalse(matches_pending_response(protocol["request"], {**protocol["handshake_response"], "selected_version": 2}))
        failed_handshake = {"protocol": "horonom.host-adapter", "request_id": "req-1", "error": {"code": "failed", "message": "driver failed"}}
        self.protocol_validator("response").validate(failed_handshake)
        self.assertTrue(self.pending_response_matches(protocol["request"], failed_handshake))
        same_id_operation_error = {**protocol["operation_error"], "request_id": "req-1"}
        self.assertFalse(self.pending_response_matches(protocol["request"], same_id_operation_error))
        same_id_handshake_error = {**protocol["unsupported_version_response"], "request_id": protocol["operation_request"]["request_id"]}
        self.assertFalse(self.pending_response_matches(protocol["operation_request"], same_id_handshake_error))
        self.assertFalse(self.pending_response_matches(protocol["request"], {**failed_handshake, "protocol_version": 1}))
        wrong = copy.deepcopy(response)
        wrong["host_contract_version"] = 2
        self.assert_invalid(root, wrong)
        bad_input = copy.deepcopy(protocol["operation_request"])
        bad_input["operation"] = "probe"
        bad_input["input"] = {}
        self.assert_invalid(root, bad_input)

    def test_manifest_open_families_ranges_and_future_compatibility(self):
        validator = self.validator("executable-adapter-manifest")
        base = load(FIXTURES / "valid-manifest-synthetic.json")
        future = copy.deepcopy(base)
        future["protocol_versions"] = [2]
        validator.validate(future)
        validate_manifest_semantics(future)
        self.assertEqual(compatibility(future), (None, "protocol_version_incompatible"))
        future["protocol_versions"] = [1, 2]
        future["capabilities"].append("future.optional.capability")
        future["host_version_constraints"][0]["provider"] = "future_unlisted_provider"
        validator.validate(future)
        validate_manifest_semantics(future)
        self.assertEqual(compatibility(future), ((1, 1), None))
        future["contract_version_range"] = {"minimum": 2, "maximum": 3}
        self.assertEqual(compatibility(future), (None, "host_contract_version_incompatible"))
        for bad_edit in (
            lambda m: m.update(protocol_versions=[]), lambda m: m.update(protocol_versions=[1, 1]),
            lambda m: m.update(protocol_versions=[True]), lambda m: m.update(protocol_versions=[1.5]),
            lambda m: m.update(contract_version_range={"minimum": 0, "maximum": 1}),
            lambda m: m.update(contract_version_range={"minimum": 3, "maximum": 2}),
            lambda m: m.update(host_version_constraints=[]),
            lambda m: m["host_version_constraints"].append(copy.deepcopy(m["host_version_constraints"][0])),
            lambda m: m["host_version_constraints"][0].update(minimum="2.0.0", maximum="1.0.0"),
            lambda m: m["host_version_constraints"][0].update(minimum="1.0.0-01", maximum=None),
            lambda m: m["host_version_constraints"][0].update(minimum="1..0", maximum=None),
        ):
            bad = copy.deepcopy(base)
            bad_edit(bad)
            if list(validator.iter_errors(bad)):
                continue
            with self.assertRaises(ValueError):
                validate_manifest_semantics(bad)

    def test_configuration_profile_and_limits(self):
        manifest = load(FIXTURES / "valid-manifest-synthetic.json")
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "properties": {"mode": {"type": "string", "enum": ["compact", "verbose"]}, "batch_size": {"type": "integer", "minimum": 1, "maximum": 64}, "nested": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string", "maxLength": 8}}}, "additionalProperties": False}}, "required": ["mode"], "additionalProperties": False}
        manifest["configuration_schema"] = schema
        validate_manifest_semantics(manifest)
        config_validator = Draft202012Validator(schema)
        for settings in ({"mode": "compact", "batch_size": 2, "nested": {"items": ["x"]}}, {}):
            if settings:
                config_validator.validate(settings)
            else:
                self.assertTrue(list(config_validator.iter_errors(settings)))
            configuration_limits(settings)
        for edit in (
            lambda s: s.update(**{"$ref": "https://example.invalid/schema"}),
            lambda s: s.update(pattern=".*"), lambda s: s.update(customValidator="exec"),
            lambda s: s.pop("additionalProperties"),
        ):
            bad = copy.deepcopy(schema)
            edit(bad)
            with self.assertRaises(ValueError):
                validate_config_profile(bad, root=True)
        for edit in (
            lambda s: s["properties"].update(x={"type": "string", "$ref": "#/definitions/x"}),
            lambda s: s["properties"].update(x={"type": "string", "unknownKeyword": True}),
        ):
            bad = copy.deepcopy(schema)
            edit(bad)
            with self.assertRaises(ValueError):
                validate_config_profile(bad, root=True)
        numeric_schema = {"$schema": schema["$schema"], "type": "object", "properties": {"value": {"type": "number"}}, "additionalProperties": False}
        for bounds in (
            {"minimum": 3, "exclusiveMaximum": 2},
            {"exclusiveMinimum": 2, "exclusiveMaximum": 2},
            {"minimum": 2, "exclusiveMinimum": 3, "maximum": 3},
        ):
            bad = copy.deepcopy(numeric_schema)
            bad["properties"]["value"].update(bounds)
            with self.assertRaises(ValueError):
                validate_config_profile(bad, root=True)
        consistent = copy.deepcopy(numeric_schema)
        consistent["properties"]["value"].update(minimum=1, exclusiveMinimum=2, exclusiveMaximum=4, maximum=5)
        validate_config_profile(consistent, root=True)
        configuration_limits({})
        with self.assertRaises(ValueError):
            configuration_limits({"x": "z" * 65537})
        no_config_schema = load(FIXTURES / "valid-manifest-synthetic.json")["configuration_schema"]
        Draft202012Validator(no_config_schema).validate({})
        deep_schema = {"$schema": schema["$schema"], "type": "object", "properties": {}, "additionalProperties": False}
        cursor = deep_schema
        for _ in range(17):
            child = {"type": "object", "properties": {}, "additionalProperties": False}
            cursor["properties"]["nested"] = child
            cursor = child
        with self.assertRaises(ValueError):
            validate_config_profile(deep_schema, root=True)
        json_depth_schema = {"$schema": schema["$schema"], "type": "object", "properties": {}, "additionalProperties": False}
        cursor = json_depth_schema
        for i in range(8):
            child = {"type": "object", "properties": {}, "additionalProperties": False}
            cursor["properties"][f"x{i}"] = child
            cursor = child
        with self.assertRaises(ValueError):
            validate_config_profile(json_depth_schema, root=True)
        json_nodes_schema = {"$schema": schema["$schema"], "type": "object", "properties": {f"p{i}": {"type": "string"} for i in range(1100)}, "additionalProperties": False}
        with self.assertRaises(ValueError):
            validate_config_profile(json_nodes_schema, root=True)
        oversized_schema = {"$schema": schema["$schema"], "type": "object", "properties": {}, "additionalProperties": False, "description": "x" * 65537}
        with self.assertRaises(ValueError):
            validate_config_profile(oversized_schema, root=True)
        literal_ref_property = {"$schema": schema["$schema"], "type": "object", "properties": {"$ref": {"type": "string"}}, "additionalProperties": False}
        validate_config_profile(literal_ref_property, root=True)
        Draft202012Validator(literal_ref_property).validate({"$ref": "ordinary value"})
        for raw in (b'{"key":1,"key":2}', b'{"key":NaN}', b'{"key":1e999}', b'{"key":', b'\xff'):
            with self.subTest(raw=raw[:20]), self.assertRaises((ValueError, UnicodeDecodeError, json.JSONDecodeError)):
                parse_bounded_json(raw)
        with self.assertRaises(ValueError):
            parse_bounded_json(b" " * 65537)
        with self.assertRaises(ValueError):
            parse_bounded_json(json.dumps({f"k{i}": i for i in range(2048)}).encode())
        nested_settings = []
        cursor = nested_settings
        for _ in range(17):
            child = []
            cursor.append(child)
            cursor = child
        with self.assertRaises(ValueError):
            parse_bounded_json(json.dumps(nested_settings).encode())

    def test_capability_states_remain_closed_and_shape_only_example_is_unknown(self):
        validator = self.validator("host-capability-snapshot")
        snapshot = load(FIXTURES / "valid-snapshot-claude-trust-required.json")
        self.assertEqual(snapshot["capabilities"][0]["state"], "trust-required")
        bad = copy.deepcopy(snapshot)
        bad["capabilities"][0]["state"] = "SUPPORTED_WITH_LIMITS"
        self.assert_invalid(validator, bad)
        shape_only = load(FIXTURES / "valid-snapshot-codex-shape-only-unknown.json")
        self.assertEqual(shape_only["capabilities"][0]["state"], "unknown")
        validator.validate(shape_only)

    def test_effective_state_precedence_uses_contextual_trust_and_fresh_evidence(self):
        trusted = {"adapter_trust": "trusted", "host_trust": "trusted"}
        unknown = {"adapter_trust": "unknown", "host_trust": "unknown"}
        full = [{"claim": "supported", "verified": True, "fresh": True, "context_match": True}]
        limited = [{"claim": "limited", "verified": True, "fresh": True, "context_match": True}]
        state, _ = effective_capability_state(unknown, {"support_evidence": full})
        self.assertEqual(state, "unknown")
        state, reasons = effective_capability_state({"adapter_trust": "untrusted", "host_trust": "unknown"}, {"trust_required": {"reason": "trust_missing"}, "support_evidence": full})
        self.assertEqual((state, reasons), ("trust-required", ["trust_missing"]))
        state, reasons = effective_capability_state({"adapter_trust": "untrusted", "host_trust": "unknown"}, {"admin_prohibition": {"verified": True, "reason": "admin_disabled"}, "trust_required": {"reason": "trust_missing"}})
        self.assertEqual(state, "admin-disabled")
        self.assertIn("trust_missing", reasons)
        state, _ = effective_capability_state(trusted, {"support_evidence": full + limited})
        self.assertEqual(state, "unknown")
        for drift in ("fresh", "context_match"):
            stale = copy.deepcopy(full)
            stale[0][drift] = False
            state, _ = effective_capability_state(trusted, {"support_evidence": stale})
            self.assertEqual(state, "unknown", drift)
        state, _ = effective_capability_state(trusted, {"unavailable": {"verified": True, "reason": "disabled"}, "support_evidence": full})
        self.assertEqual(state, "unavailable")
        state, _ = effective_capability_state(trusted, {"unresolved_prerequisites": ["profile_unknown"], "support_evidence": full})
        self.assertEqual(state, "unknown")
        self.assertEqual(effective_capability_state(trusted, {"support_evidence": limited})[0], "limited")
        disabled = {**trusted, "enabled": False}
        state, reasons = effective_capability_state(disabled, {"support_evidence": full})
        self.assertEqual((state, reasons), ("unavailable", ["disabled"]))
        self.assertEqual(effective_capability_state({**trusted, "enabled": True}, {"support_evidence": full})[0], "supported")
        drift_fixture = load(FIXTURES / "trust-drift-executable-digest.json")
        self.assertNotEqual(drift_fixture["trusted_digest"], drift_fixture["current_digest"])
        drift_fields = (
            "scope", "profile", "trust", "configuration", "manifest", "executable",
            "entrypoint", "adapter_version", "host_version",
        )
        for field in drift_fields:
            with self.subTest(drift=field):
                state, _ = effective_capability_state(trusted, {"support_evidence": [{**full[0], "drifted_fields": [field]}]})
                self.assertEqual(state, "unknown")


if __name__ == "__main__":
    unittest.main()
