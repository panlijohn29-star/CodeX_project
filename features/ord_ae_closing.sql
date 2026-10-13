select JOB_NO,CUSTOMER,MBL_NO,HBL_NO,DATE_FORMAT(ETD,"%Y-%m-%d") as ETD,
(SELECT OP_AE_JOB.HAWB_TERMS FROM OP_AE_JOB WHERE OP_AE_JOB.JOB_ID = V_JOBINFO.JOB_ID) AS SERVICE_TYPE,
(SELECT OP_AE_JOB.PAYMENT_TERMS FROM OP_AE_JOB WHERE OP_AE_JOB.JOB_ID = V_JOBINFO.JOB_ID) AS PAYMENT_TERMS,
(SELECT COUNT(distinct cf_charges.invoice_no) FROM cf_charges inner join cf_invoice on cf_charges.invoice_no = cf_invoice.invoice_no WHERE cf_charges.job_id = v_jobinfo.job_id and CF_INVOICE.INVOICE_STATUS = 2 AND cf_invoice.INVOICE_TYPE = 0 and cf_invoice.company_code = "APEX-ORD") AS AR_BILL,
(select COUNT(distinct cf_cost.invoice_no) from cf_cost inner join cf_invoice on cf_cost.invoice_no = cf_invoice.invoice_no where cf_cost.job_id = v_jobinfo.job_id and cf_invoice.invoice_status = 2 and cf_invoice.company_code = "APEX-ORD" AND cf_cost.BALANCE IN ("CNS-CASS","CARGO NETWORK SERVICES/CNS COLLECTION ACCOUNT") AND cf_invoice.INVOICE_TYPE = 1) as AP_CNS,
(select COUNT(distinct cf_cost.invoice_no) from cf_cost inner join cf_invoice on cf_cost.invoice_no = cf_invoice.invoice_no where cf_cost.job_id = v_jobinfo.job_id and cf_invoice.invoice_status = 2 and cf_invoice.company_code = "APEX-ORD" AND cf_invoice.INVOICE_TYPE = 1 and cf_cost.CHARGE_LOCAL_NAME LIKE "%PICK%") AS AP_TRUCK,
(select COUNT(distinct cf_cost.invoice_no) from cf_cost inner join cf_invoice on cf_cost.invoice_no = cf_invoice.invoice_no where cf_cost.job_id = v_jobinfo.job_id and cf_invoice.invoice_status = 2 and cf_invoice.company_code = "APEX-ORD" AND cf_invoice.INVOICE_TYPE = 1 and (cf_cost.CHARGE_LOCAL_NAME LIKE "%TRANSFER%" or cf_cost.CHARGE_LOCAL_NAME IN ("ATC"))) AS AP_ATC,
(select COUNT(distinct cf_cost.invoice_no) from cf_cost inner join cf_invoice on cf_cost.invoice_no = cf_invoice.invoice_no where cf_cost.job_id = v_jobinfo.job_id and cf_invoice.invoice_status = 2 and cf_invoice.company_code = "APEX-ORD" AND cf_cost.BALANCE = "APEX-ORD-WAREHOUSE (INTERNAL USE ONLY)" AND cf_invoice.INVOICE_TYPE = 1) AS AP_WH,
COALESCE(OCB.B_COMPLETE_CHARGES,0) as ORD_ARDONE, COALESCE(OCB.B_CHECK_CHARGES,0) AS ORD_ARCHECK, COALESCE(OCB.B_COMPLETE_COST,0) AS ORD_APDONE, COALESCE(OCB.B_CHECK_COST,0) AS ORD_APCHECK,
(select sum(exchange_usd) from cf_charges where cf_charges.job_id = v_jobinfo.job_id and cf_charges.company_code = "apex-ord") as Total_charge,
(select sum(exchange_usd) from CF_COST cf_charges where cf_charges.job_id = v_jobinfo.job_id and cf_charges.company_code = "apex-ord") as Total_cost,
((select sum(exchange_usd) from cf_charges where cf_charges.job_id = v_jobinfo.job_id and cf_charges.company_code = "apex-ord") - (select sum(exchange_usd) from CF_COST cf_charges where cf_charges.job_id = v_jobinfo.job_id and cf_charges.company_code = "apex-ord")) as Total_GP
from v_jobinfo LEFT JOIN op_charges_belong ocb ON ocb.job_id = v_jobinfo.job_id and ocb.charges_belong = "APEX-ORD"
where JOB_TYPE = "AE" AND JOB_MODE <> '02' and op_company = "APEX-ORD"
<CONDITION>
