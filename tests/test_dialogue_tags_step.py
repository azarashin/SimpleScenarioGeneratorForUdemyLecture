from copy import deepcopy
from pathlib import Path

import pytest

from pipeline.consistency import ConsistencyCheckError, PipelineConsistencyChecker
from pipeline.engine import StepExecutionEngine
from pipeline.steps import GenerateDialogueTagsStep


def _profiles():
    return [
        {
            "character_id": "c001",
            "name": "葵",
            "role": "主人公",
            "personality": {
                "core_traits": ["誠実"],
                "values": ["友情"],
                "weaknesses": ["慎重"],
            },
            "speech": {
                "style": "自然",
                "first_person": "私",
                "verbal_tics": [],
            },
            "appearance": {
                "age_impression": "成人",
                "features": ["黒髪"],
                "costume": "普段着",
            },
            "emotion_model": {
                "available_expressions": ["neutral", "smile", "angry", "confused"]
            },
        }
    ]


def _state_updates():
    return {
        "character_locations": [],
        "possessions": [],
        "known_information": [],
        "relationship_changes": [],
        "introduced_entities": [],
        "unresolved_plot_threads": [],
        "resolved_plot_threads": [],
        "continuity_summary": "葵が立ち上がり、対話を続けた。",
    }


def _sections():
    return [
        {
            "chapter_no": 1,
            "section_no": 1,
            "section_title": "対話",
            "narrative_blocks": [
                {"block_id": "n1", "type": "narration", "text": "葵は立った。", "speaker_id": None},
                {"block_id": "d1", "type": "dialogue", "text": "ありがとう。", "speaker_id": "c001"},
                {"block_id": "d2", "type": "dialogue", "text": "どういうこと？", "speaker_id": "c001"},
            ],
            "state_updates": _state_updates(),
        }
    ]


def test_step_generates_one_supported_tag_per_dialogue(make_context):
    context, _ = make_context()
    context.shared_data.update(
        {"character_profiles": _profiles(), "scenario_sections": _sections()}
    )

    output = StepExecutionEngine([GenerateDialogueTagsStep()]).run(context)

    tags = output["dialogue_expression_tags"]
    assert [tag["block_id"] for tag in tags] == ["d1", "d2"]
    assert [tag["expression"] for tag in tags] == ["smile", "confused"]
    assert all(tag["speaker_id"] == "c001" for tag in tags)
    assert all(tag["emotion_reason"] for tag in tags)
    assert (
        Path(context.artifacts_dir) / "step-05-generate-dialogue-tags.json"
    ).exists()


def test_consistency_rejects_missing_dialogue_tag():
    tags = [
        {
            "chapter_no": 1,
            "section_no": 1,
            "block_id": "d1",
            "speaker_id": "c001",
            "expression": "smile",
            "emotion_reason": "肯定的な語句",
        }
    ]
    data = {
        "character_profiles": _profiles(),
        "scenario_sections": _sections(),
    }

    with pytest.raises(ConsistencyCheckError, match="cover every dialogue block"):
        PipelineConsistencyChecker().check(
            data, {"dialogue_expression_tags": tags}
        )


def test_consistency_rejects_unsupported_expression():
    tags = []
    for section in _sections():
        for block in section["narrative_blocks"]:
            if block["type"] == "dialogue":
                tags.append(
                    {
                        "chapter_no": 1,
                        "section_no": 1,
                        "block_id": block["block_id"],
                        "speaker_id": "c001",
                        "expression": "smile",
                        "emotion_reason": "理由",
                    }
                )
    invalid = deepcopy(tags)
    invalid[0]["expression"] = "shocked"

    with pytest.raises(ConsistencyCheckError, match="does not support expression"):
        PipelineConsistencyChecker().check(
            {"character_profiles": _profiles(), "scenario_sections": _sections()},
            {"dialogue_expression_tags": invalid},
        )
