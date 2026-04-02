import datetime as d
import json
import os
import threading
import time as t
import uuid
import zipfile as z
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import pymysql


SCRIPT_ORI = """
SELECT * FROM 
(
	SELECT 
	(case when SHPT_CLOSE_STATUS = 'OPEN' AND empty_close = 0 and (COALESCE(LOCAL_ARC,0) = 0 OR COALESCE(LOCAL_APC,0) = 0) and (COALESCE(REV_CURR,0) <> 0 OR COALESCE(COST_CURR,0) <>0) THEN 'OPEN' 
	when SHPT_CLOSE_STATUS = 'OPEN' AND empty_close = 1 then 'OPEN' ELSE 'CLOSE' END) AS LOCAL_STATUS,SHPT_CLOSE_STATUS,related_office,
	
	JOB_TYPE,JOB_NO,CUSTOMER,SALES,CS,OP,MBL_NO,HBL_NO,ETD,ETA,DATE(case when job_type = 'DO' THEN ETD ELSE ETD_ETA END) AS ETD_ETA,WEIGHT,CBM,TEU,
	DATE(DELIVERY_DATE) AS DELIVERY_DATE,IFNULL(ACCOUNTING_PERIOD," ") AS ACCOUNTING_PERIOD,ATD,ATA,REV,COST,PROFIT,REV_CURR,COST_CURR,PROFIT_CURR,
	CASE WHEN empty_close = 0 and (REV_CURR<> 0 OR COST_CURR<>0) THEN 1 
	when empty_close = 1 then 1 ELSE 0 END AS CURR_PERIOD	FROM
	(
		select OP,sales, customer,CASE WHEN LENGTH(MONTH(etd)) >1 THEN CONCAT(YEAR(ETD),MONTH(ETD)) ELSE CONCAT(YEAR(ETD),"0",MONTH(ETD)) END as `MONTH`,
		date_format(etd,"%Y-%m-%d") as ETD,date_format(eta,"%Y-%m-%d") AS ETA,
		coalesce((select group_concat(CHARGES_BELONG) from op_charges_belong WHERE op_charges_belong.job_id = TT.JOB_ID),{0}) as related_office,
		(select sales_company_op as cs from op_job where op_job.job_id = TT.JOB_ID) AS CS,DATE(ETD_ETA) AS ETD_ETA,
		(case when job_type = 'AI' then (select COALESCE(OP_AI_JOB_GOODS_STATUS.DELIVERY_GOODS_IN_ETRACE_DATE,'') from OP_AI_JOB_GOODS_STATUS WHERE OP_AI_JOB_GOODS_STATUS.JOB_ID = V_JOBINFO.JOB_ID)
		WHEN JOB_TYPE = 'AE' THEN (SELECT COALESCE(OP_AE_JOB_GOODS_STATUS.DELIVERY_GOODS_DATE,'') FROM OP_AE_JOB_GOODS_STATUS WHERE OP_AE_JOB_GOODS_STATUS.JOB_ID = V_JOBINFO.JOB_ID)
		WHEN JOB_TYPE = 'OE' THEN (SELECT COALESCE(OP_OE_JOB_GOODS_STATUS.DELIVERY_GOODS_DATE,'') FROM OP_OE_JOB_GOODS_STATUS WHERE OP_OE_JOB_GOODS_STATUS.JOB_ID = V_JOBINFO.JOB_ID)
		WHEN JOB_TYPE = 'OI' THEN (SELECT COALESCE(OP_OI_JOB_GOODS_STATUS.DELIVERY_GOODS_DATE,'') FROM OP_OI_JOB_GOODS_STATUS WHERE OP_OI_JOB_GOODS_STATUS.JOB_ID = V_JOBINFO.JOB_ID)
		ELSE '' END) AS DELIVERY_DATE,V_JOBINFO.CTNR_NUM_TEU AS TEU,
		JOB_NO,MBL_NO,V_JOBINFO.ACCOUNTING_PERIOD_HISTORY AS ACCOUNTING_PERIOD,
		HBL_NO,JOB_TYPE,POL_CODE,POD_CODE,IFNULL(HAWB_GROSS_WEIGHT_KGS,WH_GROSS_WEIGHT_KGS) AS WEIGHT,HAWB_CHARGEABLE_WEIGHT AS CW,
		CASE WHEN JOB_TYPE IN ('AE','AI') THEN HAWB_VOLUME_CBM WHEN JOB_TYPE IN ('OE','OI') THEN WH_VOLUME_CBM END AS CBM,
		TT.REV,TT.COST,TT.PROFIT,TT.REV_CURR,TT.COST_CURR,TT.PROFIT_CURR,COALESCE(v_jobinfo.B_CHECK_CHARGES,0) AS AR_CLOSE,COALESCE(v_jobinfo.B_CHECK_COST,0) AS AP_CLOSE,
		(SELECT COALESCE(B_CHECK_CHARGES,0) FROM op_charges_belong WHERE CHARGES_BELONG = {0} AND op_charges_belong.JOB_ID = v_jobinfo.JOB_ID) AS LOCAL_ARC,
		(SELECT COALESCE(B_CHECK_COST,0) FROM op_charges_belong WHERE CHARGES_BELONG = {0} AND op_charges_belong.JOB_ID = v_jobinfo.JOB_ID) AS LOCAL_APC,
		case when COALESCE(v_jobinfo.B_CHECK_CHARGES,0) = 1 and COALESCE(v_jobinfo.B_CHECK_COST,0) = 1 then "CLOSED" ELSE "OPEN" END AS SHPT_CLOSE_STATUS,v_jobinfo.ACCOUNTING_PERIOD_HISTORY,
		(SELECT date_format(OP_JOB.ATA,"%Y-%m-%d") from op_job where op_job.job_id = v_jobinfo.job_id) as ATA,
		(SELECT date_format(OP_JOB.ATD,"%Y-%m-%d") from op_job where op_job.job_id = v_jobinfo.job_id) as ATD,TT.empty_close
		from v_jobinfo 
		INNER JOIN 
		(
			SELECT JOB_ID,sum(empty_close) as empty_close ,SUM(REV) AS REV,SUM(COST) AS COST,SUM(REV-COST) AS PROFIT,SUM(REV_CURR) AS REV_CURR,SUM(COST_CURR) AS COST_CURR,SUM(REV_CURR-COST_CURR) AS PROFIT_CURR
			FROM 
			(
				select cf_charges.job_id,SUM(EXCHANGE_USD) as REV, 0  as REV_CURR,
				0 AS COST,0 AS COST_CURR,0 AS empty_close
				from cf_charges
				left join op_job on cf_charges.job_id = op_job.job_id
				where CF_CHARGES.COMPANY_CODE = {0} 
				and CASE WHEN JOB_TYPE IN ('OI','AI') THEN (DATE_format(op_job.ETA,"%Y-%m-%d") >= DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR) AND DATE_format(op_job.ETA,"%Y-%m-%d") <= LAST_DAY(DATE_FORMAT(CURDATE(), '%Y-12-01')))
				else (DATE_format(op_job.ETD,"%Y-%m-%d") >= DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR) AND DATE_format(op_job.ETD,"%Y-%m-%d") <= LAST_DAY(DATE_FORMAT(CURDATE(), '%Y-12-01'))) END
				AND (CHARGE_TYPE NOT IN ('LDP00','LDPSJ','DSFSJ','SJ','HGGS','DUTY','HMF','MPF') and coalesce(cf_charges.CHARGE_CATEGORY,'') <> "paid on behalf")
				and cf_charges.ACCOUNTING_PERIOD IS NOT NULL
				group by cf_charges.job_id
				
				union all
				
				select cf_charges.job_id,0 as REV, 0 as REV_CURR,
				SUM(EXCHANGE_USD) AS COST,0 AS COST_CURR,0 AS empty_close
				from cf_cost cf_charges
				left join op_job on cf_charges.job_id = op_job.job_id
				where CF_CHARGES.COMPANY_CODE = {0} 
				and CASE WHEN JOB_TYPE IN ('OI','AI') THEN (DATE_format(op_job.ETA,"%Y-%m-%d") >= DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR) AND DATE_format(op_job.ETA,"%Y-%m-%d") <= LAST_DAY(DATE_FORMAT(CURDATE(), '%Y-12-01')))
				else (DATE_format(op_job.ETD,"%Y-%m-%d") >= DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR) AND DATE_format(op_job.ETD,"%Y-%m-%d") <= LAST_DAY(DATE_FORMAT(CURDATE(), '%Y-12-01'))) END
				AND (CHARGE_TYPE NOT IN ('LDP00','LDPSJ','DSFSJ','SJ','HGGS','DUTY','HMF','MPF') and coalesce(cf_charges.CHARGE_CATEGORY,'') <> "paid on behalf")
				and cf_charges.ACCOUNTING_PERIOD is NOT NULL
				group by cf_charges.job_id
				
				UNION ALL
				
				select cf_charges.job_id,0 as REV, EXCHANGE_USD  as REV_CURR,
				0 AS COST,0 AS COST_CURR,0 AS empty_close
				from cf_charges
				left join op_job on cf_charges.job_id = op_job.job_id
				where CF_CHARGES.COMPANY_CODE = {0} AND CF_CHARGES.ACCOUNTING_PERIOD IS NULL
				and CASE WHEN JOB_TYPE IN ('OI','AI') THEN (DATE_format(op_job.ETA,"%Y-%m-%d") >= DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR) AND DATE_format(op_job.ETA,"%Y-%m-%d") <= LAST_DAY(DATE_FORMAT(CURDATE(), '%Y-12-01')) )
				else (DATE_format(op_job.ETD,"%Y-%m-%d") >= DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR) AND DATE_format(op_job.ETD,"%Y-%m-%d") <= LAST_DAY(DATE_FORMAT(CURDATE(), '%Y-12-01')) ) END
				AND ifnull(OP_JOB.JOB_SOURCE,'x') <>7			
				UNION ALL
				select CF_COST.job_id,0,0,
				0 as COST,
				EXCHANGE_USD  as COST_CURR,
				0 AS empty_close
				from CF_COST
				left join op_job on CF_COST.job_id = op_job.job_id
				where CF_COST.COMPANY_CODE = {0} AND CF_COST.ACCOUNTING_PERIOD IS NULL
				and CASE WHEN JOB_TYPE IN ('OI','AI') THEN (DATE_format(op_job.ETA,"%Y-%m-%d") >= DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR) AND DATE_format(op_job.ETA,"%Y-%m-%d") <= LAST_DAY(DATE_FORMAT(CURDATE(), '%Y-12-01')) )
				else (DATE_format(op_job.ETD,"%Y-%m-%d") >= DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR) AND DATE_format(op_job.ETD,"%Y-%m-%d") <= LAST_DAY(DATE_FORMAT(CURDATE(), '%Y-12-01')) ) END
				AND ifnull(OP_JOB.JOB_SOURCE,'x') <>7
				
				UNION ALL
				SELECT B.JOB_ID,0,0,0,0,1 FROM 
					(
							SELECT a.*,COUNT(0) AS office_count FROM
							(
								SELECT op_job.JOB_ID
								FROM op_job 
								WHERE (op_job.B_CHECK_CHARGES = 0 or op_job.b_check_charges is null
								or op_job.B_CHECK_COST = 0 or op_job.B_CHECK_COST is null)
								AND ifnull(OP_JOB.JOB_SOURCE,'x') <>7
								AND  
									(
										EXISTS (SELECT 1 FROM cf_charges CHG WHERE op_job.JOB_ID = CHG.JOB_ID AND CHG.ACCOUNTING_PERIOD IS NULL) 
										OR EXISTS (SELECT 1 FROM cf_cost COST WHERE op_job.JOB_ID = COST.JOB_ID AND COST.ACCOUNTING_PERIOD IS NULL)
									)
							) a
							LEFT JOIN op_charges_belong ON a.JOB_ID=op_charges_belong.JOB_ID
							GROUP BY a.JOB_ID
							HAVING office_count > 1
					) b LEFT JOIN op_charges_belong ON b.job_id=op_charges_belong.JOB_ID
				WHERE op_charges_belong.CHARGES_BELONG = {0} 
				AND (op_charges_belong.B_CHECK_CHARGES IS NULL OR op_charges_belong.B_CHECK_CHARGES = 0
				OR op_charges_belong.B_CHECK_COST IS NULL OR op_charges_belong.B_CHECK_CHARGES = 0)
			)T
			GROUP BY JOB_ID
		)TT ON TT.JOB_ID = v_jobinfo.JOB_ID
		WHERE CASE WHEN JOB_TYPE IN ('OI','AI') THEN (DATE(V_JOBINFO.ETA) >= DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR) AND DATE_format(V_JOBINFO.ETA,"%Y-%m-%d") <= DATE_format(NOW(),"%Y-%m-%d"))
		ELSE (DATE(V_JOBINFO.ETD) >= DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR) AND DATE_format(V_JOBINFO.ETD,"%Y-%m-%d") <= DATE_format(NOW(),"%Y-%m-%d")) END
	)x
)xx
WHERE 1=1 
ORDER BY LOCAL_STATUS,JOB_TYPE,ETD,ETA
"""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_ROOT = os.path.join(BASE_DIR, "reports_v4")
MAX_WORKERS = 4
RUNS = {}
RUNS_LOCK = threading.Lock()

DB_CONFIG = {
    "scdbca": {
        "host": "aauw-db-us-sc.mysql.database.azure.com",
        "port": 3306,
        "user": "john",
        "password": "john123456#A",
        "db": "scdbca",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.SSDictCursor,
        "connect_timeout": 10,
        "read_timeout": 600,
        "write_timeout": 600,
    },
    "scdbus": {
        "host": "aauw-db-us-sc.mysql.database.azure.com",
        "port": 3306,
        "user": "john",
        "password": "john123456#A",
        "db": "scdbus",
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.SSDictCursor,
        "connect_timeout": 10,
        "read_timeout": 600,
        "write_timeout": 600,
    },
}

GROUPS = [
    {
        "label": "NA reports",
        "db_name": "scdbus",
        "offices": [
            "APEX-ORD", "APEX-USA", "APEX-LAX", "APEX-JFK", "APEX-MIA",
            "APEX-SEA", "APEX-SFO", "APEX-DFW", "APEX-ECM", "APEX-NA",
            "APEX-LCK", "PERI-LAX", "PERI-SFO",
        ],
    },
    {
        "label": "YYZ reports",
        "db_name": "scdbca",
        "offices": ["APEX-YYZ"],
    },
]


def ensure_report_root():
    os.makedirs(REPORT_ROOT, exist_ok=True)


def fetch_dataframe(connection, query):
    with connection.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()
    return pd.DataFrame(rows)


def get_office_groups():
    return [{"label": group["label"], "offices": list(group["offices"])} for group in GROUPS]


def _now_iso():
    return d.datetime.now().isoformat(timespec="seconds")


def _write_manifest(run_info):
    manifest_path = os.path.join(run_info["output_dir"], "manifest.json")
    manifest = {
        "run_id": run_info["id"],
        "status": run_info["status"],
        "created_at": run_info["created_at"],
        "updated_at": run_info["updated_at"],
        "completed_tasks": run_info["completed_tasks"],
        "total_tasks": run_info["total_tasks"],
        "message": run_info["message"],
        "zip_name": run_info.get("zip_name"),
        "zip_path": run_info.get("zip_path"),
        "files": run_info.get("files", []),
        "error": run_info.get("error"),
        "selected_offices": run_info.get("selected_offices", []),
        "cancel_requested": run_info.get("cancel_requested", False),
    }
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=True, indent=2)


def _build_selected_groups(selected_offices):
    office_set = set(selected_offices)
    selected_groups = []
    for group in GROUPS:
        offices = [office for office in group["offices"] if office in office_set]
        if offices:
            selected_groups.append({
                "label": group["label"],
                "db_name": group["db_name"],
                "offices": offices,
            })
    return selected_groups


def create_run(selected_offices):
    ensure_report_root()
    run_id = uuid.uuid4().hex[:12]
    run_dir = os.path.join(REPORT_ROOT, run_id)
    os.makedirs(run_dir, exist_ok=True)
    selected_groups = _build_selected_groups(selected_offices)
    total_tasks = sum(len(group["offices"]) for group in selected_groups)
    run_info = {
        "id": run_id,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "status": "queued",
        "message": "Waiting to start",
        "completed_tasks": 0,
        "total_tasks": total_tasks,
        "files": [],
        "zip_name": None,
        "zip_path": None,
        "error": None,
        "output_dir": run_dir,
        "selected_groups": selected_groups,
        "selected_offices": list(selected_offices),
        "cancel_requested": False,
        "cancel_event": threading.Event(),
        "active_connections": {},
    }
    with RUNS_LOCK:
        RUNS[run_id] = run_info
    _write_manifest(run_info)
    return run_info


def _snapshot(run_info):
    snapshot = dict(run_info)
    snapshot.pop("cancel_event", None)
    snapshot.pop("active_connections", None)
    snapshot.pop("selected_groups", None)
    return snapshot


def update_run(run_id, **kwargs):
    with RUNS_LOCK:
        run_info = RUNS[run_id]
        run_info.update(kwargs)
        run_info["updated_at"] = _now_iso()
        snapshot = _snapshot(run_info)
    _write_manifest(snapshot)
    return snapshot


def get_run(run_id):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        return _snapshot(run_info) if run_info else None


def list_runs():
    ensure_report_root()
    runs = []
    for run_id in os.listdir(REPORT_ROOT):
        manifest_path = os.path.join(REPORT_ROOT, run_id, "manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        with open(manifest_path, "r", encoding="utf-8") as handle:
            runs.append(json.load(handle))
    runs.sort(key=lambda item: item["created_at"], reverse=True)
    return runs


def _register_connection(run_id, office, db_name, connection):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        if run_info:
            run_info["active_connections"][office] = {
                "db_name": db_name,
                "thread_id": connection.thread_id(),
                "connection": connection,
            }


def _unregister_connection(run_id, office):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        if run_info:
            run_info["active_connections"].pop(office, None)


def _kill_db_thread(db_name, thread_id):
    kill_connection = pymysql.connect(**DB_CONFIG[db_name])
    try:
        with kill_connection.cursor() as cursor:
            cursor.execute("KILL {0}".format(int(thread_id)))
    finally:
        kill_connection.close()


def _should_cancel(run_id):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        return bool(run_info and run_info["cancel_event"].is_set())


def cancel_run(run_id):
    with RUNS_LOCK:
        run_info = RUNS.get(run_id)
        if not run_info:
            return None
        run_info["cancel_requested"] = True
        run_info["cancel_event"].set()
        active_connections = list(run_info["active_connections"].values())
        finished = run_info["status"] in ("completed", "failed", "cancelled")
    for connection_info in active_connections:
        try:
            _kill_db_thread(connection_info["db_name"], connection_info["thread_id"])
        except Exception:
            pass
        try:
            connection_info["connection"].close()
        except Exception:
            pass
    if finished:
        return get_run(run_id)
    return update_run(run_id, status="cancelling", message="Cancellation requested")


def create_report(run_id, office, db_name, output_dir):
    if _should_cancel(run_id):
        raise RuntimeError("Run cancelled by user")
    office_sql = '"{0}"'.format(office)
    script_fnl = SCRIPT_ORI.format(office_sql)
    excel_name = "{0} closing report.xlsx".format(office)
    excel_path = os.path.join(output_dir, excel_name)

    cnxn = pymysql.connect(**DB_CONFIG[db_name])
    _register_connection(run_id, office, db_name, cnxn)
    try:
        if _should_cancel(run_id):
            raise RuntimeError("Run cancelled by user")
        df = fetch_dataframe(cnxn, script_fnl)
    finally:
        _unregister_connection(run_id, office)
        try:
            cnxn.close()
        except Exception:
            pass

    if _should_cancel(run_id):
        raise RuntimeError("Run cancelled by user")
    df.to_excel(excel_path, index=False)
    return excel_name


def run_report_group(run_id, label, db_name, office_list, output_dir, generated_files):
    worker_count = min(MAX_WORKERS, len(office_list))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(create_report, run_id, office, db_name, output_dir): office
            for office in office_list
        }
        try:
            for future in as_completed(future_map):
                office = future_map[future]
                file_name = future.result()
                generated_files.append(file_name)
                current = get_run(run_id)
                message = "Completed {0} for {1}".format(office, label)
                update_run(
                    run_id,
                    status="running",
                    message=message,
                    completed_tasks=current["completed_tasks"] + 1,
                    files=sorted(generated_files),
                )
                if _should_cancel(run_id):
                    raise RuntimeError("Run cancelled by user")
        except Exception:
            for future in future_map:
                future.cancel()
            raise


def zip_reports(output_dir, files):
    zip_name = "YYZ_NA_closing_report_{0}.zip".format(str(d.date.today()))
    zip_path = os.path.join(output_dir, zip_name)
    with z.ZipFile(zip_path, "w") as zip_file:
        for file_name in files:
            file_path = os.path.join(output_dir, file_name)
            zip_file.write(file_path, arcname=file_name, compress_type=z.ZIP_DEFLATED)
    return zip_name, zip_path


def execute_run(run_id):
    started_at = t.time()
    run_info = get_run(run_id)
    output_dir = run_info["output_dir"]
    generated_files = []

    update_run(run_id, status="running", message="Connecting to databases")

    try:
        if run_info["total_tasks"] == 0:
            raise RuntimeError("Please select at least one office")

        with RUNS_LOCK:
            selected_groups = list(RUNS[run_id]["selected_groups"])

        for group in selected_groups:
            if _should_cancel(run_id):
                raise RuntimeError("Run cancelled by user")
            update_run(run_id, status="running", message="Running {0}".format(group["label"]))
            run_report_group(run_id, group["label"], group["db_name"], group["offices"], output_dir, generated_files)

        if _should_cancel(run_id):
            raise RuntimeError("Run cancelled by user")
        zip_name, zip_path = zip_reports(output_dir, generated_files)
        duration = str(d.timedelta(seconds=(t.time() - started_at)))
        update_run(
            run_id,
            status="completed",
            message="Finished in {0}".format(duration),
            files=sorted(generated_files),
            zip_name=zip_name,
            zip_path=zip_path,
        )
    except Exception as exc:
        current = get_run(run_id)
        was_cancelled = current and current.get("cancel_requested")
        status = "cancelled" if was_cancelled or "cancelled by user" in str(exc).lower() else "failed"
        message = "Run cancelled" if status == "cancelled" else "Run failed"
        update_run(
            run_id,
            status=status,
            message=message,
            error=str(exc),
        )


def start_run(selected_offices):
    run_info = create_run(selected_offices)
    thread = threading.Thread(target=execute_run, args=(run_info["id"],), daemon=True)
    thread.start()
    return run_info["id"]
