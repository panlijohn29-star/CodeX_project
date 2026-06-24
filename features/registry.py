import features.closing_report as closing_report


FEATURES = {
    closing_report.FEATURE["id"]: closing_report.FEATURE,
}


def list_features():
    return [
        {
            "id": feature["id"],
            "title": feature["title"],
            "category": feature["category"],
            "description": feature["description"],
            "supports_cancel": feature.get("supports_cancel", False),
            "output_type": feature.get("output_type", "files"),
            "input_schema": feature.get("input_schema", []),
        }
        for feature in FEATURES.values()
    ]


def get_feature(feature_id):
    return FEATURES.get(feature_id)
