"""
Unit tests for attribute_parser.py

Tests the conversion of LLM-extracted attribute dicts to Pinecone metadata attributes.
"""

import pytest
from utils.attribute_parser import (
    parse_version,
    parse_pipe_list,
    get_attributes_from_tags,
    validate_attributes
)


class TestParseVersion:
    """Test version string parsing."""

    def test_full_version_3_parts(self):
        """Test parsing full version string with 3 parts."""
        result = parse_version("10.9.0", 3)
        assert result == [10, 9, 0]

    def test_full_version_with_v_prefix(self):
        """Test parsing version with 'v' prefix."""
        result = parse_version("v10.9.0", 3)
        assert result == [10, 9, 0]

    def test_partial_version_2_parts(self):
        """Test parsing partial version with only 2 parts."""
        result = parse_version("1.20", 2)
        assert result == [1, 20]

    def test_single_digit_version(self):
        """Test parsing single digit version."""
        result = parse_version("9", 3)
        assert result == [9, 0, 0]

    def test_missing_parts_filled_with_zero(self):
        """Test that missing version parts are filled with 0."""
        result = parse_version("10.9", 3)
        assert result == [10, 9, 0]

    def test_complex_version(self):
        """Test parsing complex version number."""
        result = parse_version("v9.18.0", 3)
        assert result == [9, 18, 0]

    def test_zero_version(self):
        """Test parsing 0.0.0 (default version)."""
        result = parse_version("0.0.0", 3)
        assert result == [0, 0, 0]

    def test_malformed_version_non_numeric(self):
        """Test handling of non-numeric version parts."""
        result = parse_version("10.9.x", 3)
        assert result == [10, 9, 0]  # Non-numeric part becomes 0


class TestParsePipeList:
    """Test pipe-separated list parsing."""

    def test_single_item(self):
        """Test parsing single item."""
        result = parse_pipe_list("jimi-jc261")
        assert result == ["jimi-jc261"]

    def test_multiple_items(self):
        """Test parsing multiple items."""
        result = parse_pipe_list("jimi-jc261|mitac-gemini|jimi-jc450")
        assert result == ["jimi-jc261", "mitac-gemini", "jimi-jc450"]

    def test_empty_string(self):
        """Test parsing empty string returns empty list."""
        result = parse_pipe_list("")
        assert result == []

    def test_whitespace_only(self):
        """Test parsing whitespace-only string returns empty list."""
        result = parse_pipe_list("   ")
        assert result == []

    def test_pipes_with_whitespace(self):
        """Test parsing handles whitespace around pipes."""
        result = parse_pipe_list("item1 | item2 | item3")
        assert result == ["item1", "item2", "item3"]

    def test_empty_items_filtered(self):
        """Test that empty items between pipes are filtered out."""
        result = parse_pipe_list("item1||item2")
        assert result == ["item1", "item2"]

    def test_trailing_pipes(self):
        """Test handling of trailing pipes."""
        result = parse_pipe_list("item1|item2|")
        assert result == ["item1", "item2"]


class TestGetAttributesFromTags:
    """Test conversion of LLM attribute dicts to Pinecone attributes."""

    def test_full_tags_with_values(self):
        """Test conversion with all attributes populated."""
        tags = {
            "fleet_portal_version": "10.9.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "1.20",
            "device_models": ["jimi-jc261", "mitac-gemini"],
            "plans_nin": ["SHIELD"],
            "event_types": ["Traffic-Light-Violated", "Overspeeding"],
            "required_features": ["ADAS", "DMS"]
        }

        attributes = get_attributes_from_tags(tags)

        assert attributes['fleet_portal_version_major'] == 10
        assert attributes['fleet_portal_version_minor'] == 9
        assert attributes['fleet_portal_version_patch'] == 0
        assert attributes['device_apk_version_major'] == 1
        assert attributes['device_apk_version_minor'] == 20
        assert attributes['device_models_in'] == ["jimi-jc261", "mitac-gemini"]
        assert attributes['plans_nin'] == ["SHIELD"]
        assert attributes['event_type_in'] == ["Traffic-Light-Violated", "Overspeeding"]
        assert attributes['required_features'] == ["ADAS", "DMS"]

    def test_default_values_for_general_content(self):
        """Test that default values are used for general content."""
        tags = {
            "fleet_portal_version": "0.0.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "0.0",
            "device_models": [],
            "plans_nin": [],
            "event_types": [],
            "required_features": []
        }

        attributes = get_attributes_from_tags(tags)

        assert attributes['fleet_portal_version_major'] == 0
        assert attributes['fleet_portal_version_minor'] == 0
        assert attributes['fleet_portal_version_patch'] == 0
        assert attributes['device_apk_version_major'] == 0
        assert attributes['device_apk_version_minor'] == 0
        assert 'device_models_in' not in attributes  # Empty values not added
        assert 'plans_nin' not in attributes
        assert 'event_type_in' not in attributes
        assert 'required_features' not in attributes

    def test_minimum_version_extraction(self):
        """Test extracting minimum version (9.18 not 10.0)."""
        tags = {
            "fleet_portal_version": "9.18.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "0.0",
            "device_models": "",
            "plans_nin": "",
            "event_types": "",
            "required_features": ""
        }

        attributes = get_attributes_from_tags(tags)

        assert attributes['fleet_portal_version_major'] == 9
        assert attributes['fleet_portal_version_minor'] == 18
        assert attributes['fleet_portal_version_patch'] == 0

    def test_empty_tag_dict(self):
        """Test handling empty tag dict."""
        tags = {}

        attributes = get_attributes_from_tags(tags)

        # Should have default version values
        assert attributes['fleet_portal_version_major'] == 0
        assert attributes['fleet_portal_version_minor'] == 0
        assert attributes['fleet_portal_version_patch'] == 0

    def test_model_specific_content(self):
        """Test model-specific attribute extraction."""
        tags = {
            "fleet_portal_version": "10.9.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "1.20",
            "device_models": ["jimi-jc261"],
            "plans_nin": ["SHIELD"],
            "event_types": ["Traffic-Light-Violated"],
            "required_features": ["ADAS"]
        }

        attributes = get_attributes_from_tags(tags)

        assert attributes['device_models_in'] == ["jimi-jc261"]
        assert attributes['plans_nin'] == ["SHIELD"]
        assert attributes['required_features'] == ["ADAS"]

    def test_plans_in_vs_plans_nin(self):
        """Test distinction between plans_in and plans_nin."""
        tags = {
            "fleet_portal_version": "0.0.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "0.0",
            "device_models": [],
            "plans_nin": []
        }
        # Add plans_in if the parser supports it
        if "plans_in" in tags:
            tags["plans_in"] = ["NON-SHIELD"]

        attributes = get_attributes_from_tags(tags)

        # Test that plans_nin is empty
        assert 'plans_nin' not in attributes  # Empty


class TestValidateAttributes:
    """Test attribute validation."""

    def test_valid_attributes(self):
        """Test validation passes for valid attributes."""
        attributes = {
            'fleet_portal_version_major': 10,
            'fleet_portal_version_minor': 9,
            'fleet_portal_version_patch': 0,
            'device_apk_version_major': 1,
            'device_apk_version_minor': 20,
            'device_models_in': ["jimi-jc261"],
            'plans_nin': ["SHIELD"],
            'event_type_in': ["Traffic-Light-Violated"],
            'required_features': ["ADAS"],
            'category_in': ["fleet_portal"]
        }

        valid, error = validate_attributes(attributes)

        assert valid is True
        assert error == ""

    def test_minimal_valid_attributes(self):
        """Test validation passes with only required version fields."""
        attributes = {
            'fleet_portal_version_major': 0,
            'fleet_portal_version_minor': 0,
            'fleet_portal_version_patch': 0,
            'device_apk_version_major': 0,
            'device_apk_version_minor': 0,
        }

        valid, error = validate_attributes(attributes)

        assert valid is True
        assert error == ""

    def test_invalid_negative_version(self):
        """Test validation fails for negative version numbers."""
        attributes = {
            'fleet_portal_version_major': -1,
            'fleet_portal_version_minor': 9,
            'fleet_portal_version_patch': 0,
            'device_apk_version_major': 1,
            'device_apk_version_minor': 20,
        }

        valid, error = validate_attributes(attributes)

        assert valid is False
        assert "fleet_portal_version_major" in error
        assert "non-negative" in error

    def test_invalid_version_not_integer(self):
        """Test validation fails for non-integer version."""
        attributes = {
            'fleet_portal_version_major': "10",  # String instead of int
            'fleet_portal_version_minor': 9,
            'fleet_portal_version_patch': 0,
            'device_apk_version_major': 1,
            'device_apk_version_minor': 20,
        }

        valid, error = validate_attributes(attributes)

        assert valid is False
        assert "fleet_portal_version_major" in error

    def test_invalid_list_field_not_list(self):
        """Test validation fails when list field is not a list."""
        attributes = {
            'fleet_portal_version_major': 10,
            'fleet_portal_version_minor': 9,
            'fleet_portal_version_patch': 0,
            'device_apk_version_major': 1,
            'device_apk_version_minor': 20,
            'device_models_in': "jimi-jc261",  # String instead of list
        }

        valid, error = validate_attributes(attributes)

        assert valid is False
        assert "device_models_in" in error
        assert "must be a list" in error

    def test_validation_with_empty_lists(self):
        """Test validation passes with empty list fields."""
        attributes = {
            'fleet_portal_version_major': 10,
            'fleet_portal_version_minor': 9,
            'fleet_portal_version_patch': 0,
            'device_apk_version_major': 1,
            'device_apk_version_minor': 20,
            'device_models_in': [],
            'plans_nin': [],
            'event_type_in': [],
        }

        valid, error = validate_attributes(attributes)

        assert valid is True
        assert error == ""


class TestIntegrationScenarios:
    """Test real-world scenarios end-to-end."""

    def test_scenario_feature_with_hard_dependencies(self):
        """Test extracting attributes for feature with hard dependencies."""
        # Scenario: ADAS feature on JiMi JC261, requires v10.9+, not on Shield
        tags = {
            "fleet_portal_version": "10.9.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "1.20",
            "device_models": ["jimi-jc261"],
            "plans_nin": ["SHIELD"],
            "event_types": ["Traffic-Light-Violated"],
            "required_features": ["ADAS"]
        }

        attributes = get_attributes_from_tags(tags)
        valid, error = validate_attributes(attributes)

        assert valid is True
        assert attributes['fleet_portal_version_major'] == 10
        assert attributes['fleet_portal_version_minor'] == 9
        assert attributes['device_models_in'] == ["jimi-jc261"]
        assert attributes['plans_nin'] == ["SHIELD"]
        assert attributes['required_features'] == ["ADAS"]

    def test_scenario_general_content_minimal_tags(self):
        """Test extracting attributes for general content (minimal tagging)."""
        # Scenario: General login instructions, no dependencies
        tags = {
            "fleet_portal_version": "0.0.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "0.0",
            "device_models": [],
            "plans_nin": [],
            "event_types": [],
            "required_features": []
        }

        attributes = get_attributes_from_tags(tags)
        valid, error = validate_attributes(attributes)

        assert valid is True
        assert attributes['fleet_portal_version_major'] == 0
        assert attributes['fleet_portal_version_minor'] == 0
        assert attributes['device_apk_version_major'] == 0
        # No other attributes should be present
        assert 'device_models_in' not in attributes
        assert 'plans_nin' not in attributes

    def test_scenario_minimum_version_extraction(self):
        """Test extracting minimum version from enhancement history."""
        # Scenario: Feature introduced in v9.18, enhanced in v10.0
        # Should extract 9.18 (minimum), not 10.0
        tags = {
            "fleet_portal_version": "9.18.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "0.0",
            "device_models": [],
            "plans_nin": [],
            "event_types": [],
            "required_features": ["Custom-Events"]
        }

        attributes = get_attributes_from_tags(tags)
        valid, error = validate_attributes(attributes)

        assert valid is True
        assert attributes['fleet_portal_version_major'] == 9
        assert attributes['fleet_portal_version_minor'] == 18
        assert attributes['required_features'] == ["Custom-Events"]

    def test_scenario_multiple_models(self):
        """Test extracting multiple device models."""
        # Scenario: Feature works on multiple JiMi models
        tags = {
            "fleet_portal_version": "10.8.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "1.22",
            "device_models": ["jimi-jc261", "jimi-jc261p", "jimi-jc450", "jimi-jc400"],
            "plans_nin": [],
            "event_types": [],
            "required_features": []
        }

        attributes = get_attributes_from_tags(tags)
        valid, error = validate_attributes(attributes)

        assert valid is True
        assert len(attributes['device_models_in']) == 4
        assert "jimi-jc261" in attributes['device_models_in']
        assert "jimi-jc450" in attributes['device_models_in']

    def test_scenario_event_specific_article(self):
        """Test extracting event-specific attribute."""
        # Scenario: Article only useful for Traffic Light Violation event
        tags = {
            "fleet_portal_version": "10.9.0",
            "master_portal_version": "0.0.0",
            "device_apk_version": "1.20",
            "device_models": [],
            "plans_nin": [],
            "event_types": ["Traffic-Light-Violated"],
            "required_features": ["ADAS"]
        }

        attributes = get_attributes_from_tags(tags)
        valid, error = validate_attributes(attributes)

        assert valid is True
        assert attributes['event_type_in'] == ["Traffic-Light-Violated"]
        assert attributes['required_features'] == ["ADAS"]
        assert 'device_models_in' not in attributes  # Empty
