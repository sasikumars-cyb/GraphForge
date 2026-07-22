"""Unit tests for ContextBuilder."""

from app.ai.services.context_builder import AIContext, ContextBuilder
from app.analysis.models.impact import (
    DependencyPath,
    DependencyPathStep,
    ImpactAnalysisResult,
    ImpactedNode,
    RiskLevel,
)


def _node(name: str, node_type: str = "Service") -> ImpactedNode:
    return ImpactedNode(id=f"r1:{name}", name=name, node_type=node_type, repository_id="r1")


def _path(*names: str) -> DependencyPath:
    return DependencyPath(
        steps=[
            DependencyPathStep(
                node_id=f"r1:{n}",
                node_name=n,
                node_type="Service",
                relationship="CALLS" if i > 0 else None,
            )
            for i, n in enumerate(names)
        ]
    )


def _analysis(
    risk: RiskLevel = RiskLevel.MEDIUM,
    direct: list[ImpactedNode] | None = None,
    indirect: list[ImpactedNode] | None = None,
    apis: list[ImpactedNode] | None = None,
    topics: list[ImpactedNode] | None = None,
    libraries: list[ImpactedNode] | None = None,
    paths: list[DependencyPath] | None = None,
) -> ImpactAnalysisResult:
    return ImpactAnalysisResult(
        risk=risk,
        directly_impacted_services=direct or [],
        indirectly_impacted_services=indirect or [],
        impacted_apis=apis or [],
        impacted_topics=topics or [],
        impacted_libraries=libraries or [],
        dependency_paths=paths or [],
    )


def test_build_empty_context() -> None:
    ctx = ContextBuilder().build()
    assert isinstance(ctx, AIContext)
    assert ctx.repository_name == ""
    assert ctx.changed_files == []
    assert ctx.jira_issues == []


def test_with_repository() -> None:
    ctx = (
        ContextBuilder()
        .with_repository(name="order-svc", owner="acme", default_branch="main")
        .build()
    )
    assert ctx.repository_name == "order-svc"
    assert ctx.repository_owner == "acme"
    assert ctx.default_branch == "main"


def test_with_pull_request() -> None:
    ctx = (
        ContextBuilder()
        .with_pull_request(title="Fix bug", number=42, head_ref="fix/bug", base_ref="main")
        .build()
    )
    assert ctx.pull_request_title == "Fix bug"
    assert ctx.pull_request_number == 42
    assert ctx.head_ref == "fix/bug"
    assert ctx.base_ref == "main"


def test_with_analysis() -> None:
    result = _analysis(
        risk=RiskLevel.HIGH,
        direct=[_node("OrderService")],
        indirect=[_node("PaymentService")],
        apis=[_node("GET /orders", "Endpoint")],
        topics=[_node("order-events", "KafkaTopic")],
        libraries=[_node("commons-lib", "MavenDependency")],
        paths=[_path("OrderService", "PaymentService")],
    )
    ctx = ContextBuilder().with_analysis(result).build()

    assert ctx.risk == "HIGH"
    assert len(ctx.directly_impacted_services) == 1
    assert ctx.directly_impacted_services[0]["name"] == "OrderService"
    assert len(ctx.indirectly_impacted_services) == 1
    assert ctx.indirectly_impacted_services[0]["name"] == "PaymentService"
    assert len(ctx.impacted_apis) == 1
    assert len(ctx.impacted_topics) == 1
    assert len(ctx.impacted_libraries) == 1
    assert len(ctx.dependency_paths) == 1
    assert ctx.dependency_paths[0][0]["node_name"] == "OrderService"
    assert ctx.dependency_paths[0][1]["relationship"] == "CALLS"


def test_with_changed_files() -> None:
    files = ["src/orders.py", "src/payments.py"]
    ctx = ContextBuilder().with_changed_files(files).build()
    assert ctx.changed_files == files


def test_with_jira_issues() -> None:
    issues = [{"key": "PROJ-123", "summary": "Fix order bug"}]
    ctx = ContextBuilder().with_jira_issues(issues).build()
    assert ctx.jira_issues == issues


def test_fluent_chaining() -> None:
    result = _analysis(risk=RiskLevel.LOW)
    ctx = (
        ContextBuilder()
        .with_repository(name="svc", owner="org", default_branch="main")
        .with_pull_request(title="PR", number=1, head_ref="feat", base_ref="main")
        .with_analysis(result)
        .with_changed_files(["a.py"])
        .with_jira_issues([])
        .build()
    )
    assert ctx.repository_name == "svc"
    assert ctx.pull_request_title == "PR"
    assert ctx.risk == "LOW"
    assert ctx.changed_files == ["a.py"]


def test_truncate_file_list_within_budget() -> None:
    files = ["short.py"]
    ctx = ContextBuilder(max_context_chars=100).with_changed_files(files).build()
    assert ctx.changed_files == files


def test_truncate_file_list_exceeds_budget() -> None:
    files = [f"src/very/long/path/to/file_{i}.py" for i in range(1000)]
    ctx = ContextBuilder(max_context_chars=200).with_changed_files(files).build()
    # Should have fewer files than the full list
    assert len(ctx.changed_files) < len(files)
    assert len(ctx.changed_files) > 0


def test_changed_files_not_mutated() -> None:
    original = ["a.py", "b.py"]
    builder = ContextBuilder().with_changed_files(original)
    original.append("c.py")
    ctx = builder.build()
    assert ctx.changed_files == ["a.py", "b.py"]


def test_impacted_node_dict_shape() -> None:
    result = _analysis(direct=[_node("Svc", "Controller")])
    ctx = ContextBuilder().with_analysis(result).build()
    node_dict = ctx.directly_impacted_services[0]
    assert set(node_dict.keys()) == {"id", "name", "node_type"}
    assert node_dict["node_type"] == "Controller"


def test_to_prompt_variables_repository() -> None:
    ctx = (
        ContextBuilder()
        .with_repository(name="order-svc", owner="acme", default_branch="main")
        .with_pull_request(title="Fix bug", number=7, head_ref="fix/x", base_ref="main")
        .build()
    )
    variables = ctx.to_prompt_variables()
    assert variables["repository"] == "acme/order-svc"
    assert variables["pull_request_title"] == "Fix bug"


def test_to_prompt_variables_changed_files() -> None:
    ctx = ContextBuilder().with_changed_files(["a.py", "b.py"]).build()
    variables = ctx.to_prompt_variables()
    assert "a.py" in variables["changed_files"]
    assert "b.py" in variables["changed_files"]


def test_to_prompt_variables_deterministic_analysis() -> None:
    result = _analysis(risk=RiskLevel.HIGH, direct=[_node("OrderService")])
    ctx = ContextBuilder().with_analysis(result).build()
    variables = ctx.to_prompt_variables()
    assert "HIGH" in variables["deterministic_analysis"]
    assert "OrderService" in variables["deterministic_analysis"]


def test_to_prompt_variables_impacted_components() -> None:
    result = _analysis(
        direct=[_node("A")],
        apis=[_node("GET /a", "Endpoint")],
    )
    ctx = ContextBuilder().with_analysis(result).build()
    variables = ctx.to_prompt_variables()
    assert "A" in variables["impacted_components"]
    assert "GET /a" in variables["impacted_components"]


def test_to_prompt_variables_dependency_paths() -> None:
    result = _analysis(paths=[_path("Svc1", "Svc2")])
    ctx = ContextBuilder().with_analysis(result).build()
    variables = ctx.to_prompt_variables()
    assert "Svc1" in variables["dependency_paths"]
    assert "Svc2" in variables["dependency_paths"]
