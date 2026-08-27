from app.application.mock_exams import MockExamGenerationService


def test_specialized_allocation_gives_more_slots_to_observed_weak_points() -> None:
    counts = MockExamGenerationService._specialized_target_counts(
        [
            ("WEAK", 0.2, 5),
            ("MEDIUM", 0.6, 5),
            ("STRONG", 0.95, 5),
            ("NO_EVIDENCE", 1.0, 0),
        ],
        total=12,
    )

    assert sum(counts.values()) == 12
    assert counts["WEAK"] > counts["MEDIUM"]
    assert counts["MEDIUM"] > counts["STRONG"]
    assert counts["WEAK"] > counts["NO_EVIDENCE"]
