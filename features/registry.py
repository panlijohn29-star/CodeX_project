import features.closing_report as closing_report
import features.ar_ap_breakdown as ar_ap_breakdown
import features.offset_invoice as offset_invoice
import features.archive_currency_invoice as archive_currency_invoice
import features.related_office_modification as related_office_modification
import features.sql_query as sql_query
import features.eason_dfw_billing as eason_dfw_billing
import features.eason_client_report as eason_client_report
import features.ord_ae_closing as ord_ae_closing
import features.truck_rate as truck_rate


FEATURES = {
    closing_report.FEATURE["id"]: closing_report.FEATURE,
    ar_ap_breakdown.FEATURE["id"]: ar_ap_breakdown.FEATURE,
    offset_invoice.FEATURE["id"]: offset_invoice.FEATURE,
    archive_currency_invoice.FEATURE["id"]: archive_currency_invoice.FEATURE,
    related_office_modification.FEATURE["id"]: related_office_modification.FEATURE,
    sql_query.FEATURE["id"]: sql_query.FEATURE,
    eason_dfw_billing.FEATURE["id"]: eason_dfw_billing.FEATURE,
    eason_client_report.FEATURE["id"]: eason_client_report.FEATURE,
    ord_ae_closing.FEATURE["id"]: ord_ae_closing.FEATURE,
    truck_rate.FEATURE["id"]: truck_rate.FEATURE,
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
