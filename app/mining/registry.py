"""Registry of process-discovery algorithms.

Every entry describes one algorithm adapter: identity, implementation provider
and version, input/output types, a parameter schema used for validation and
form generation, preconditions, limitations, compatible evaluation methods,
supported exports and availability. Availability is decided from what is
actually installed; nothing is substituted silently.

Texts shown to users (names of parameters, limitations, reasons) are catalog
keys under ``algo.*`` so the UI can render them in the selected language.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import lru_cache
from importlib import metadata
from typing import Any

ADAPTER_VERSION = "1"

# Output (native) model types
PROCESS_TREE = "process_tree"
PETRI_NET = "petri_net"
HEURISTICS_NET = "heuristics_net"
POWL = "powl"
DECLARE = "declare"
LOG_SKELETON = "log_skeleton"
TRANSITION_SYSTEM = "transition_system"
TEMPORAL_PROFILE = "temporal_profile"

# Evaluation methods
TOKEN_REPLAY = "token_replay"
ALIGNMENTS = "alignments"
SOUNDNESS = "soundness"
DECLARE_CONF = "declare_conformance"
LOG_SKELETON_CONF = "log_skeleton_conformance"
TEMPORAL_CONF = "temporal_profile_conformance"
PETRI_EVALUATIONS = [TOKEN_REPLAY, ALIGNMENTS, SOUNDNESS]


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str  # float | int | bool | choice | multichoice | activities
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    exclusive_maximum: bool = False
    exclusive_minimum: bool = False
    choices: tuple = ()
    help_key: str = ""
    only_with: dict | None = None  # parameter is valid only when another parameter has one of these values


@dataclass(frozen=True)
class AlgorithmSpec:
    id: str
    name_key: str
    family: str
    provider: str
    output_type: str
    params: tuple[ParamSpec, ...] = ()
    input_types: tuple[str, ...] = ("case_event_log",)
    # representations derived from the native model by semantics-preserving conversions
    derived_representations: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    evaluations: tuple[str, ...] = ()
    exports: tuple[str, ...] = ("json", "png")
    requires_module: str | None = "pm4py"
    unavailable_reason: str | None = None  # catalog key; set => never available
    needs_real_timestamps: bool = False
    deterministic: bool = True


def provider_version(module: str = "pm4py") -> str | None:
    try:
        return metadata.version(module)
    except metadata.PackageNotFoundError:
        return None


DECLARE_TEMPLATES = (
    "existence", "absence", "exactly_one", "init", "responded_existence", "coexistence", "response", "precedence",
    "succession", "altresponse", "altprecedence", "altsuccession", "chainresponse", "chainprecedence",
    "chainsuccession", "noncoexistence", "nonsuccession", "nonchainsuccession",
)

_ALGORITHMS: tuple[AlgorithmSpec, ...] = (
    AlgorithmSpec(
        id="inductive", name_key="algo.inductive.name", family="imperative", provider="pm4py",
        output_type=PROCESS_TREE, derived_representations=(PETRI_NET, "bpmn"),
        params=(ParamSpec("disable_fallthroughs", "bool", False, help_key="algo.param.disable_fallthroughs"),),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.inductive", "algo.lim.block_structured"),
        evaluations=tuple(PETRI_EVALUATIONS), exports=("ptml", "pnml", "bpmn", "json", "png"),
    ),
    AlgorithmSpec(
        id="inductive_infrequent", name_key="algo.inductive_infrequent.name", family="imperative", provider="pm4py",
        output_type=PROCESS_TREE, derived_representations=(PETRI_NET, "bpmn"),
        params=(
            ParamSpec("noise_threshold", "float", 0.2, 0.0, 1.0, help_key="algo.param.noise_threshold_im"),
            ParamSpec("disable_fallthroughs", "bool", False, help_key="algo.param.disable_fallthroughs"),
        ),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.imf", "algo.lim.block_structured"),
        evaluations=tuple(PETRI_EVALUATIONS), exports=("ptml", "pnml", "bpmn", "json", "png"),
    ),
    AlgorithmSpec(
        id="inductive_directly_follows", name_key="algo.inductive_directly_follows.name", family="imperative",
        provider="pm4py", output_type=PROCESS_TREE, derived_representations=(PETRI_NET, "bpmn"),
        input_types=("case_event_log", "directly_follows_graph"),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.imd", "algo.lim.block_structured"),
        evaluations=tuple(PETRI_EVALUATIONS), exports=("ptml", "pnml", "bpmn", "json", "png"),
    ),
    AlgorithmSpec(
        id="heuristics", name_key="algo.heuristics.name", family="imperative", provider="pm4py",
        output_type=HEURISTICS_NET, derived_representations=(PETRI_NET,),
        params=(
            ParamSpec("dependency_threshold", "float", 0.5, 0.0, 1.0, help_key="algo.param.dependency_threshold"),
            ParamSpec("and_threshold", "float", 0.65, 0.0, 1.0, help_key="algo.param.and_threshold"),
            ParamSpec("loop_two_threshold", "float", 0.5, 0.0, 1.0, help_key="algo.param.loop_two_threshold"),
            ParamSpec("min_act_count", "int", 1, 1, None, help_key="algo.param.min_act_count"),
            ParamSpec("min_dfg_occurrences", "int", 1, 1, None, help_key="algo.param.min_dfg_occurrences"),
        ),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.heuristics",),
        evaluations=tuple(PETRI_EVALUATIONS), exports=("pnml", "json", "png"),
    ),
    AlgorithmSpec(
        id="heuristics_plusplus", name_key="algo.heuristics_plusplus.name", family="imperative", provider="pm4py",
        output_type=HEURISTICS_NET, derived_representations=(PETRI_NET,),
        params=(
            ParamSpec("dependency_threshold", "float", 0.5, 0.0, 1.0, help_key="algo.param.dependency_threshold"),
            ParamSpec("and_threshold", "float", 0.65, 0.0, 1.0, help_key="algo.param.and_threshold"),
            ParamSpec("min_act_count", "int", 1, 1, None, help_key="algo.param.min_act_count"),
            ParamSpec("min_dfg_occurrences", "int", 1, 1, None, help_key="algo.param.min_dfg_occurrences"),
        ),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.heuristics", "algo.lim.heuristics_plusplus"),
        evaluations=tuple(PETRI_EVALUATIONS), exports=("pnml", "json", "png"),
    ),
    AlgorithmSpec(
        id="alpha", name_key="algo.alpha.name", family="imperative", provider="pm4py", output_type=PETRI_NET,
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.alpha",),
        evaluations=tuple(PETRI_EVALUATIONS), exports=("pnml", "json", "png"),
    ),
    AlgorithmSpec(
        id="alpha_plus", name_key="algo.alpha_plus.name", family="imperative", provider="pm4py", output_type=PETRI_NET,
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.alpha_plus",),
        evaluations=tuple(PETRI_EVALUATIONS), exports=("pnml", "json", "png"),
    ),
    AlgorithmSpec(
        id="powl", name_key="algo.powl.name", family="imperative", provider="pm4py", output_type=POWL,
        derived_representations=(PETRI_NET,),
        params=(
            ParamSpec("variant", "choice", "MAXIMAL", choices=("MAXIMAL", "DYNAMIC_CLUSTERING", "BRUTE_FORCE", "TREE"),
                      help_key="algo.param.powl_variant"),
            ParamSpec("filtering_weight_factor", "float", 0.0, 0.0, 1.0, exclusive_maximum=True,
                      help_key="algo.param.filtering_weight_factor"),
            ParamSpec("order_graph_filtering_threshold", "float", None, 0.5, 1.0, exclusive_minimum=True,
                      help_key="algo.param.order_graph_filtering_threshold",
                      only_with={"variant": ("DYNAMIC_CLUSTERING",)}),
        ),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.powl",),
        evaluations=tuple(PETRI_EVALUATIONS), exports=("pnml", "json", "png"),
    ),
    AlgorithmSpec(
        id="declare", name_key="algo.declare.name", family="declarative", provider="pm4py", output_type=DECLARE,
        params=(
            ParamSpec("min_support_ratio", "float", 0.3, 0.0, 1.0, help_key="algo.param.min_support_ratio"),
            ParamSpec("min_confidence_ratio", "float", 0.9, 0.0, 1.0, help_key="algo.param.min_confidence_ratio"),
            ParamSpec("allowed_templates", "multichoice", [], choices=DECLARE_TEMPLATES,
                      help_key="algo.param.allowed_templates"),
            ParamSpec("considered_activities", "activities", [], help_key="algo.param.considered_activities"),
        ),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.declare",),
        evaluations=(DECLARE_CONF,), exports=("json",),
    ),
    AlgorithmSpec(
        id="log_skeleton", name_key="algo.log_skeleton.name", family="declarative", provider="pm4py",
        output_type=LOG_SKELETON,
        params=(ParamSpec("noise_threshold", "float", 0.0, 0.0, 1.0, help_key="algo.param.noise_threshold_ls"),),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.log_skeleton",),
        evaluations=(LOG_SKELETON_CONF,), exports=("json",),
    ),
    AlgorithmSpec(
        id="transition_system", name_key="algo.transition_system.name", family="state_based", provider="pm4py",
        output_type=TRANSITION_SYSTEM,
        params=(
            ParamSpec("direction", "choice", "forward", choices=("forward", "backward"), help_key="algo.param.ts_direction"),
            ParamSpec("window", "int", 2, 1, 10, help_key="algo.param.ts_window"),
            ParamSpec("view", "choice", "sequence", choices=("sequence", "set", "multiset"), help_key="algo.param.ts_view"),
        ),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.transition_system",),
        evaluations=(), exports=("json", "png"),
    ),
    AlgorithmSpec(
        id="temporal_profile", name_key="algo.temporal_profile.name", family="temporal", provider="pm4py",
        output_type=TEMPORAL_PROFILE, needs_real_timestamps=True,
        preconditions=("algo.pre.nonempty", "algo.pre.timestamps"),
        limitations=("algo.lim.temporal_profile",),
        evaluations=(TEMPORAL_CONF,), exports=("json",),
    ),
    # --- research adapters ----------------------------------------------------------
    AlgorithmSpec(
        id="ilp", name_key="algo.ilp.name", family="research_region", provider="pm4py", output_type=PETRI_NET,
        params=(ParamSpec("alpha", "float", 1.0, 0.0, 1.0, help_key="algo.param.ilp_alpha"),),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.ilp",),
        evaluations=tuple(PETRI_EVALUATIONS), exports=("pnml", "json", "png"),
    ),
    AlgorithmSpec(
        id="genetic", name_key="algo.genetic.name", family="research_evolutionary", provider="pm4py",
        output_type=PETRI_NET, deterministic=False,
        params=(
            ParamSpec("population_size", "int", 100, 2, 2000, help_key="algo.param.population_size"),
            ParamSpec("generations", "int", 30, 1, 1000, help_key="algo.param.generations"),
            ParamSpec("elitism_rate", "float", 0.01, 0.0, 1.0, help_key="algo.param.elitism_rate"),
            ParamSpec("crossover_rate", "float", 1.0, 0.0, 1.0, help_key="algo.param.crossover_rate"),
            ParamSpec("mutation_rate", "float", 0.01, 0.0, 1.0, help_key="algo.param.mutation_rate"),
            ParamSpec("elitism_min_sample", "int", 5, 1, 1000, help_key="algo.param.elitism_min_sample"),
            ParamSpec("seed", "int", 42, 0, 2**31 - 1, help_key="algo.param.seed"),
        ),
        preconditions=("algo.pre.nonempty",),
        limitations=("algo.lim.genetic",),
        evaluations=tuple(PETRI_EVALUATIONS), exports=("pnml", "json", "png"),
    ),
    AlgorithmSpec(
        id="split_miner", name_key="algo.split_miner.name", family="research_split", provider="none",
        output_type="bpmn", requires_module=None, unavailable_reason="algo.unavailable.split_miner",
    ),
    AlgorithmSpec(
        id="fuzzy_miner", name_key="algo.fuzzy_miner.name", family="research_fuzzy", provider="none",
        output_type="fuzzy_graph", requires_module=None, unavailable_reason="algo.unavailable.fuzzy_miner",
    ),
)

ALGORITHMS: dict[str, AlgorithmSpec] = {a.id: a for a in _ALGORITHMS}


@lru_cache
def _module_available(module: str) -> bool:
    return provider_version(module) is not None


def availability(spec: AlgorithmSpec) -> dict:
    if spec.unavailable_reason:
        return {"status": "unavailable", "reason_key": spec.unavailable_reason}
    if spec.requires_module and not _module_available(spec.requires_module):
        return {"status": "unavailable", "reason_key": "algo.unavailable.missing_module",
                "reason_params": {"module": spec.requires_module}}
    return {"status": "available", "reason_key": None}


def implementation(spec: AlgorithmSpec) -> dict:
    from app import __version__

    return {"provider": spec.provider, "provider_version": provider_version(spec.provider) if spec.provider != "none" else None,
            "adapter_version": ADAPTER_VERSION, "processlens_version": __version__}


def describe(spec: AlgorithmSpec) -> dict:
    data = asdict(spec)
    data["params"] = [asdict(p) for p in spec.params]
    data["availability"] = availability(spec)
    data["implementation"] = implementation(spec)
    return data


def get(algorithm_id: str) -> AlgorithmSpec:
    spec = ALGORITHMS.get(algorithm_id)
    if spec is None:
        raise KeyError(algorithm_id)
    return spec


class ParamError(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__("; ".join(f"{e['param']}: {e['problem']}" for e in errors))
        self.errors = errors


def validate_params(spec: AlgorithmSpec, raw: dict | None, activities: set[str] | None = None) -> dict:
    """Validate and normalize parameters. Unknown parameters are rejected (never ignored)."""
    raw = dict(raw or {})
    errors: list[dict] = []
    known = {p.name for p in spec.params}
    for name in sorted(set(raw) - known):
        errors.append({"param": name, "problem": "not supported by this algorithm"})
    out: dict = {}
    for p in spec.params:
        value = raw.get(p.name, p.default)
        if value is None or value == "":
            out[p.name] = p.default
            continue
        try:
            if p.type == "float":
                value = float(value)
            elif p.type == "int":
                if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                    raise ValueError
                value = int(value)
            elif p.type == "bool":
                if isinstance(value, str):
                    if value.lower() not in ("true", "false"):
                        raise ValueError
                    value = value.lower() == "true"
                elif not isinstance(value, bool):
                    raise ValueError
            elif p.type == "choice":
                if value not in p.choices:
                    errors.append({"param": p.name, "problem": f"must be one of {list(p.choices)}"})
                    continue
            elif p.type in ("multichoice", "activities"):
                if isinstance(value, str):
                    value = [v for v in value.split(",") if v]
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise ValueError
                value = sorted(set(value))
                if p.type == "multichoice" and set(value) - set(p.choices):
                    errors.append({"param": p.name, "problem": f"unknown values {sorted(set(value) - set(p.choices))}"})
                    continue
                if p.type == "activities" and activities is not None and set(value) - activities:
                    errors.append({"param": p.name, "problem": f"unknown activities {sorted(set(value) - activities)}"})
                    continue
        except (TypeError, ValueError):
            errors.append({"param": p.name, "problem": f"expected {p.type}"})
            continue
        if p.type in ("float", "int"):
            if p.minimum is not None and (value <= p.minimum if p.exclusive_minimum else value < p.minimum):
                errors.append({"param": p.name, "problem": f"must be {'>' if p.exclusive_minimum else '>='} {p.minimum}"})
                continue
            if p.maximum is not None and (value >= p.maximum if p.exclusive_maximum else value > p.maximum):
                errors.append({"param": p.name, "problem": f"must be {'<' if p.exclusive_maximum else '<='} {p.maximum}"})
                continue
        out[p.name] = value
    for p in spec.params:
        if p.only_with and out.get(p.name) is not None:
            for other, allowed in p.only_with.items():
                if out.get(other) not in allowed:
                    errors.append({"param": p.name, "problem": f"only valid when {other} is one of {list(allowed)}"})
    if errors:
        raise ParamError(errors)
    return out


def evaluations_for(model_type: str, has_petri_net: bool) -> list[str]:
    if model_type == DECLARE:
        return [DECLARE_CONF]
    if model_type == LOG_SKELETON:
        return [LOG_SKELETON_CONF]
    if model_type == TEMPORAL_PROFILE:
        return [TEMPORAL_CONF]
    return list(PETRI_EVALUATIONS) if has_petri_net else []
