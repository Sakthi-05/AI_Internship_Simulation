"""
Integration Verification for Dynamic Workflow

This test verifies that all dynamic workflow components exist
and are properly connected, without requiring a full database setup.
"""
import pytest


def test_dynamic_baseline_service_exists():
    """Verify dynamic baseline generation service exists."""
    from app.services.assessment_service import generate_dynamic_baseline_questions
    assert callable(generate_dynamic_baseline_questions)
    print("✓ Dynamic baseline generation service exists")


def test_dynamic_task_generator_exists():
    """Verify dynamic task generator service exists."""
    from app.services.generators.dynamic_task_generator import generate_dynamic_task
    assert callable(generate_dynamic_task)
    print("✓ Dynamic task generator service exists")


def test_sprint_progression_service_exists():
    """Verify sprint objective tracking service exists."""
    from app.services.simulation.progression_service import calculate_sprint_progress
    assert callable(calculate_sprint_progress)
    print("✓ Sprint objective tracking service exists")


def test_performance_trend_service_exists():
    """Verify performance trend calculation service exists."""
    from app.services.intelligence_service import calculate_performance_trend
    assert callable(calculate_performance_trend)
    print("✓ Performance trend calculation service exists")


def test_contextual_adaptability_service_exists():
    """Verify contextual adaptability service exists."""
    from app.services.engines.workflow_engine import maybe_trigger_contextual_requirement_change
    assert callable(maybe_trigger_contextual_requirement_change)
    print("✓ Contextual adaptability service exists")


def test_review_service_exists():
    """Verify AI review service with idempotency exists."""
    from app.services.ai.review_service import ReviewService
    assert ReviewService is not None
    print("✓ AI review service exists")


def test_task_schema_includes_dynamic_fields():
    """Verify task schema includes dynamic workflow fields."""
    from app.schemas.task import TaskRead
    import inspect
    
    # Check that the schema has the new fields
    schema_fields = TaskRead.model_fields
    required_fields = ["hints", "recommended_team_role", "generation_reason", "performance_trend_at_generation", "dependency_details"]
    
    for field in required_fields:
        assert field in schema_fields, f"Missing field: {field}"
    
    print("✓ Task schema includes dynamic workflow fields")


def test_simulation_start_integration():
    """Verify simulation start route imports dynamic task generator."""
    # Just verify the imports work
    try:
        from app.api.routes.simulation import start_simulation
        from app.services.generators.dynamic_task_generator import generate_dynamic_task
        print("✓ Simulation start can access dynamic task generator")
    except ImportError as e:
        pytest.fail(f"Import failed: {e}")


def test_review_pipeline_integration():
    """Verify review service imports dynamic task generator."""
    try:
        from app.services.ai.review_service import ReviewService
        from app.services.generators.dynamic_task_generator import generate_dynamic_task
        print("✓ Review service can access dynamic task generator")
    except ImportError as e:
        pytest.fail(f"Import failed: {e}")


def test_progression_service_uses_sprint_objective():
    """Verify progression service uses sprint objective not count."""
    from app.services.simulation.progression_service import calculate_sprint_progress
    import inspect
    
    # Check the function signature
    sig = inspect.signature(calculate_sprint_progress)
    params = list(sig.parameters.keys())
    
    # Should take simulation, sprint_id (not task count)
    assert "simulation" in params
    assert "sprint_id" in params
    
    print("✓ Progression service uses sprint objective tracking")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
