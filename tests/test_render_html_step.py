import json
from pathlib import Path

from pipeline.engine import StepExecutionEngine
from pipeline.steps import RenderHtmlStep, build_minimal_steps


def _state_updates():
    return {
        "character_locations": [],
        "possessions": [],
        "known_information": [],
        "relationship_changes": [],
        "introduced_entities": [],
        "unresolved_plot_threads": [],
        "resolved_plot_threads": [],
        "completed_event_ids": [],
        "continuity_summary": "葵が雨の中で出発を決めた。",
    }


def test_render_html_step_is_registered_after_dialogue_tags():
    assert [step.name for step in build_minimal_steps()][-2:] == [
        "step-05-generate-dialogue-tags",
        "step-06-render-html",
    ]


def test_render_html_step_writes_pages_and_manifest(make_context):
    context, _ = make_context()
    run_root = Path(context.artifacts_dir).parent
    image_path = run_root / "assets" / "characters" / "c001" / "base.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    context.shared_data.update(
        {
            "character_profiles": [{"character_id": "c001", "name": "葵"}],
            "scenario_outline": {
                "title": "物語",
                "logline": "二人の物語",
                "chapters": [
                    {
                        "chapter_no": 1,
                        "chapter_title": "出会い",
                        "chapter_goal": "協力する",
                        "sections": [
                            {
                                "section_no": 1,
                                "section_title": "雨",
                                "section_purpose": "出会う",
                                "key_events": [
                                    {
                                        "event_id": "phase-1-beat-1",
                                        "description": "二人が出会う。",
                                    }
                                ],
                                "participating_characters": ["c001"],
                            }
                        ],
                    }
                ],
            },
            "scenario_sections": [
                {
                    "chapter_no": 1,
                    "section_no": 1,
                    "section_title": "雨",
                    "narrative_blocks": [
                        {
                            "block_id": "d1",
                            "type": "dialogue",
                            "text": "行こう。",
                            "speaker_id": "c001",
                        }
                    ],
                    "state_updates": _state_updates(),
                }
            ],
            "dialogue_expression_tags": [
                {
                    "chapter_no": 1,
                    "section_no": 1,
                    "block_id": "d1",
                    "speaker_id": "c001",
                    "expression": "neutral",
                    "emotion_reason": "中立",
                }
            ],
            "character_image_assets": [
                {
                    "character_id": "c001",
                    "base_image_path": "assets/characters/c001/base.png",
                    "expression_images": {
                        "neutral": "assets/characters/c001/base.png"
                    },
                }
            ],
        }
    )

    result = RenderHtmlStep().run(context)

    manifest = result.output["rendered_html_pages"]
    assert manifest["index_path"] == "index.html"
    assert manifest["chapter_pages"][0]["path"] == "chapter-1/index.html"
    assert manifest["section_pages"][0]["path"] == "chapter-1/section-1.html"
    assert (run_root / "index.html").is_file()
    assert (run_root / "chapter-1" / "index.html").is_file()
    section_html = (run_root / "chapter-1" / "section-1.html").read_text(
        encoding="utf-8"
    )
    assert "葵" in section_html
    assert 'src="../assets/characters/c001/base.png"' in section_html
    assert result.metadata["validated_link_count"] == 6
    assert result.metadata["validated_image_count"] == 1


def test_render_html_step_auto_loads_generated_json_and_image_assets(make_context):
    context, trace = make_context()
    run_root = Path(context.artifacts_dir).parent
    image_path = run_root / "assets" / "characters" / "c001" / "base.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    artifacts = {
        "step-01-generate-character-profiles.json": {
            "character_profiles": [{"character_id": "c001", "name": "葵"}]
        },
        "step-02-generate-outline.json": {
            "scenario_outline": {
                "title": "物語",
                "logline": "二人の物語",
                "chapters": [
                    {
                        "chapter_no": 1,
                        "chapter_title": "出会い",
                        "chapter_goal": "協力する",
                        "sections": [
                            {
                                "section_no": 1,
                                "section_title": "雨",
                                "section_purpose": "出会う",
                                "key_events": [
                                    {
                                        "event_id": "phase-1-beat-1",
                                        "description": "二人が出会う。",
                                    }
                                ],
                                "participating_characters": ["c001"],
                            }
                        ],
                    }
                ],
            }
        },
        "step-03-generate-character-images.json": {
            "character_image_assets": [
                {
                    "character_id": "c001",
                    "base_image_path": "assets/characters/c001/base.png",
                    "expression_images": {
                        "neutral": "assets/characters/c001/base.png"
                    },
                }
            ]
        },
        "step-04-generate-sections.json": {
            "scenario_sections": [
                {
                    "chapter_no": 1,
                    "section_no": 1,
                    "section_title": "雨",
                    "narrative_blocks": [
                        {
                            "block_id": "d1",
                            "type": "dialogue",
                            "text": "行こう。",
                            "speaker_id": "c001",
                        }
                    ],
                    "state_updates": _state_updates(),
                }
            ]
        },
        "step-05-generate-dialogue-tags.json": {
            "dialogue_expression_tags": [
                {
                    "chapter_no": 1,
                    "section_no": 1,
                    "block_id": "d1",
                    "speaker_id": "c001",
                    "expression": "neutral",
                    "emotion_reason": "中立",
                }
            ]
        },
    }
    artifacts_dir = Path(context.artifacts_dir)
    artifacts_dir.mkdir(parents=True)
    for filename, payload in artifacts.items():
        (artifacts_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    output = StepExecutionEngine([RenderHtmlStep()]).run(context)

    assert "rendered_html_pages" in output
    assert context.shared_data["character_profiles"][0]["name"] == "葵"
    loaded = [
        event for event in trace.events if event.get("event") == "artifacts_auto_loaded"
    ]
    assert len(loaded) == 1
    assert set(loaded[0]["outputs"]) == {
        "character_profiles",
        "scenario_outline",
        "character_image_assets",
        "scenario_sections",
        "dialogue_expression_tags",
    }
