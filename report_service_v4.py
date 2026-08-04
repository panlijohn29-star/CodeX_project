from features.closing_report import get_office_groups
from run_service import cancel_run, get_run, list_runs
from run_service import start_run as _start_platform_run


def start_run(selected_offices):
    return _start_platform_run("closing_report", {"offices": selected_offices})
