import pymysql
import pandas as pd
import zipfile as z
import time as t
import datetime as d
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from platform_config import get_db_config
# from O365 import Message, Attachment
#INTERNAL 192.168.0.24
#EXTERNAL 64.60.13.218

script_ori = """
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
							SELECT a.*,COUNT(0) AS 涉及分站数 FROM
							(
								SELECT op_job.JOB_ID
-- 								,op_job.JOB_NO,op_job.B_CHECK_CHARGES AS job级别arclose,op_job.B_CHECK_COST AS job级别apclose,op_job.INVOICE_STATUS 
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
							HAVING 涉及分站数>1
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

year = 2023
Ex_list = []
S_Total = t.time()
MAX_WORKERS = 4
PRINT_LOCK = threading.Lock()

def connect_db(db_name):
    config = get_db_config(db_name)
    config["cursorclass"] = pymysql.cursors.SSDictCursor
    return pymysql.connect(**config)

def fetch_dataframe(connection, query):
    with connection.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()
    return pd.DataFrame(rows)


def safe_print(message):
    with PRINT_LOCK:
        print(message)


def render_progress(completed, total, label):
    bar_width = 30
    filled = int(bar_width * completed / total)
    bar = '#' * filled + '-' * (bar_width - filled)
    safe_print("[{0}] {1}/{2} {3}".format(bar, completed, total, label))

def create_report(office, db_name):
    office_sql = '"{0}"'.format(office)
    script_fnl = script_ori.format(office_sql)
    start_t = t.time()
    safe_print("Begin task for {0} office ...".format(office))
    safe_print("Searching data on DB for {0} ...".format(office))

    cnxn = connect_db(db_name)
    try:
        df = fetch_dataframe(cnxn, script_fnl)
    finally:
        cnxn.close()

    safe_print("Creating Excel file for {0} ...".format(office))
    excel_n = "{0} closing report.xlsx".format(office)
    df.to_excel(excel_n, columns=None, header=True, index=False)

    end_t = t.time()
    db_ex = str(d.timedelta(seconds=(end_t - start_t)))
    safe_print("{0} office's task completed".format(office))
    safe_print("It take {0} to complete {1}'s task".format(db_ex, office))
    return excel_n


def run_report_group(office_list, db_name, label):
    worker_count = min(MAX_WORKERS, len(office_list))
    total = len(office_list)
    completed = 0
    render_progress(completed, total, label)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(create_report, office, db_name): office
            for office in office_list
        }
        completed_files = {}
        for future in as_completed(future_map):
            office = future_map[future]
            completed_files[office] = future.result()
            completed += 1
            render_progress(completed, total, label)
    return [completed_files[office] for office in office_list]


def YYZ_RPT():
    office_list = ['APEX-YYZ']
    Ex_list.extend(run_report_group(office_list, 'scdbca', 'YYZ reports'))



def NA_RPT():
    #office_list = ['PERI-LAX','PERI-SFO']
    office_list = ['APEX-ORD','APEX-USA','APEX-LAX','APEX-JFK','APEX-MIA','APEX-SEA','APEX-SFO','APEX-DFW','APEX-ECM','APEX-NA','APEX-LCK','PERI-LAX','PERI-SFO']
    Ex_list.extend(run_report_group(office_list, 'scdbus', 'NA reports'))

NA_RPT()
YYZ_RPT()

zip_file_name = 'YYZ_NA_closing report_{0}.zip'.format(str(d.date.today()))

print("Zipping...")
with z.ZipFile(zip_file_name, 'w') as zipF:
    for file in Ex_list:
        zipF.write(file, compress_type=z.ZIP_DEFLATED)

E_Total = t.time()
print("Zip file created.")
print("The total time for all office's task is: {0}".format(str(d.timedelta(seconds=(E_Total - S_Total)))))
