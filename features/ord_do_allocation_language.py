"""Shared language preference for the converter and launcher."""



LANGUAGE = "zh"


def set_language(value, persist=False):
    global LANGUAGE
    LANGUAGE = value if value in ("zh", "en") else "zh"


def choose(zh, en):
    return zh if LANGUAGE == "zh" else en


VALIDATION_ZH = {
    "IWT_HANDLING_OFFICE_MULTIPLE": "该 H# 在 XC 对应多个非 ORD handling office，已全部列出，请核查。",
    "IWT_CW_NOT_FOUND": "WMS 的 H# 在 XC 未找到重量记录，CW 留空，请核查后再使用账单。",
    "IWT_CW_DUPLICATE": "H# 在 XC 有多条重量记录，无法唯一匹配，CW 留空，请核查。",
    "IWT_CW_INVALID": "H# 在 XC 的重量为空、非有效数字或非正数，CW 留空，请核查。",
    "IWT_DUPLICATE_HBL": "WMS 中同一 IWT/H# 重复出现，仅保留一条以免重复分摊，请核查。",
    "RAW_MISSING_REQUIRED": "原始行缺少 DO#、PRO# 或有效金额。",
    "RAW_DUPLICATE_DO": "同一 DO# 在原始表重复出现；查找字段沿用第一条映射。",
    "RAW_PRO_AMOUNT_CONFLICT": "同一 PRO# 存在不同账单金额。请核对原始数据后再使用账单。",
    "RAW_DO_MAPPING_CONFLICT": "同一 DO# 对应不同 PRO#/金额。请修正原始映射后重跑。",
    "CARRIER_FILE_UNREADABLE": "无法读取运输商查找文件，请检查文件及权限。",
    "CARRIER_FILE_NO_MATCH": "运输商文件没有可识别的 PRO#/carrier 行。",
    "MISSING_HBL": "数据库返回了空 H#，请检查对应 DO。",
    "SC_JOB_NOT_FOUND": "SC 未找到该 H# 的 job/customer。",
    "MISSING_PRO_AMOUNT": "该 PRO# 没有有效的原始账单金额。",
    "INVALID_PRO_CW": "本分摊组没有正数 CW，无法进行分摊。",
    "INVALID_H_CW": "该 H# 的 CW 为空、零或负数，分摊金额为 0。",
    "PRO_TOTAL_MISMATCH": "PRO# 分摊总额与原始金额不符，请核对明细。",
    "HANDLING_OFFICE_MISSING": "DO 开头的单号缺少 dest_handing_office。",
    "HANDLING_OFFICE_AR_MAPPING_MISSING": "非 APEX-ORD 的 handling office 没有对应 AR billing party 映射。",
    "DB_DO_NOT_FOUND": "XC dispatch、WMS IWT 及 SC fallback 均未找到该 DO#。",
    "REMARK_ALLOCATION_INVALID": "无法明确识别指定金额备注，或备注指向其他 DO。请修正后重跑。",
    "REMARK_ALLOCATION_CONFLICT": "指定 DO 金额有冲突、超出账单总额，或全部指定后仍有未分配余额。本 PRO 分摊金额留空，请修正后重跑。",
    "REMARK_ALLOCATION_UNFULFILLED": "指定金额未完成分摊：缺少 shipment、CW 无效或金额不符。请检查对应 DO/HBL。",
    "REMARK_ALLOCATION_APPLIED": "备注指定金额已核对一致。DO 内按 HBL CW 分摊；余额仅在未指定 DO 中分摊。",
}


def validation_message(code, english):
    if LANGUAGE == "zh" and code in VALIDATION_ZH:
        if code == "PRO_TOTAL_MISMATCH":
            return VALIDATION_ZH[code] + " " + english
        return VALIDATION_ZH[code]
    return english


REPLACEMENTS = {
    "Querying XC handling office for IWT H#...": "正在按 IWT H# 查询 XC handling office...",
    "Reading Raw workbook": "读取原始工作簿",
    "Reading debug/reference workbook": "读取测试参考工作簿",
    "Loading database configuration...": "正在加载数据库配置...",
    "Querying XC dispatch / WMS IWT data": "正在查询 XC dispatch / WMS IWT 数据",
    "dispatch-like DO": "普通 DO",
    "Querying SC fallback for": "正在查询 SC 补充数据，单数：",
    "DO(s) not found in XC/WMS...": "（XC/WMS 未找到的 DO）...",
    "Querying SC job/customer info for": "正在查询 SC job/customer，记录数：",
    "calculation row(s)...": "条 calculation 记录...",
    "Querying IWT destination/to-firm info...": "正在查询 IWT 收货方...",
    "Allocating cost by chargeable weight and building sheets...": "正在按备注指定金额及 CW 分摊并生成工作表...",
    "Saving workbook": "保存工作簿",
    "Found ": "找到 ", "Raw row(s).": "条原始记录。",
    "Created: ": "已生成：", "Validation summary: ": "校验汇总：",
    "ERROR": "错误", "WARNING": "警告", "INFO": "提示",
    "Cannot find source headers. Required fields: PRO#/vendor invoice number, AMT, DO#.": "找不到原始表头，需要 PRO#、AMT/TRUCK COST、DO#。",
    "Cannot open input workbook": "无法打开输入工作簿",
    "No worksheet contains the required Raw headers.": "没有工作表包含所需的原始表头。",
    "Raw workbook has headers, but no usable data rows.": "原始表有表头，但没有有效数据行。",
    "PyMySQL is not installed. Run pip install -r requirements.txt.": "缺少 PyMySQL，请运行 pip install -r requirements.txt。",
    "python-dotenv is not installed. Run pip install -r requirements.txt.": "缺少 python-dotenv，请运行 pip install -r requirements.txt。",
    "Database configuration is incomplete for": "数据库配置不完整：",
    "Missing:": "缺少：", "must be an integer.": "必须为整数。",
    "XC database query failed": "XC 数据库查询失败",
    "WMS database query failed": "WMS 数据库查询失败",
    "Configuration file not found": "找不到数据库配置文件",
    "SC fallback query failed": "SC 补充查询失败",
    "database query failed": "数据库查询失败",
    "Input file not found": "找不到输入文件",
    "--use-existing-output must contain a calculation sheet.": "参考文件必须包含 calculation 工作表。",
    "Conversion failed": "转换失败",
}


def translate(text):
    if LANGUAGE == "en":
        return text
    for english, chinese in REPLACEMENTS.items():
        text = text.replace(english, chinese)
    return text
