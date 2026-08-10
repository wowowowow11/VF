""" ****************************************************************************
**    Author:  DuanZhaobing                                                   **
**    e-mail:  duanzb@waythink.cn                                             **
**    Date:    24.03.08-26.08.4                                              **
**    Version: 1.2.0.0                                                        **
**    Project: VF65                                                           **
"""

""" **************** zcanpro模块说明 ****************

# ZCANPRO程序中提供了zcanpro模块，使用"import zcanpro"导入至自定义的脚本中即可使用。

# 提供的接口如下：
1. buses = zcanpro.get_buses()
    ...（此处省略原模块说明）
"""

import json
import random
import datetime
import zcanpro
import time, threading
import os
import ctypes

# 加载安全算法库
current_dir = os.path.dirname(os.path.abspath(__file__))
dll = ctypes.CDLL(os.path.join(current_dir, "VF65ZLGDll.dll"))

# 全局变量
timeDelaySec = 0.05
stopTask = False
seedLen = 16  # 随机种子长度
seed = [0x00] * seedLen  # 初始化种子数组
project_name = "VF65"

# 诊断寻址配置
DiagnosticAddressing = {
    "PhysicalAddressing": 0x688,
    "FunctionalAddressing": 0x6FF,
    "ResponseAddressing": 0x608
}

# 诊断服务 ID (SID)
UDSSID = {
    "DiagnosticSessionControlService": 0x10,
    "ECUResetservice": 0x11,
    "ClearDiagnosticInformationservice": 0x14,
    "ReadDTCInformationResponseservice": 0x19,
    "ReadDataByIdentifierservice": 0x22,
    "SecurityAccessservice": 0x27,
    "CommunicationControlservice": 0x28,
    "ControlDTCSettingservice": 0x85,
    "WriteDataByIdentifierservice": 0x2E,
    "TesterPresentservice": 0x3E,
    "InputOutputControlByIdentifier": 0x2F,
    "RoutinueControl": 0x31,
}

# 消极响应代码 (NRC)
NRCDefinition = {
    "serviceNotSupported": 0x11,
    "subfunctionNotSupported": 0x12,
    "incorrectMessageLengthOrInvalidFormat": 0x13,
    "conditionsNotCorrect": 0x22,
    "requestOutOfRange": 0x31,
    "securityAccessDenied": 0x33,
    "invaledKey": 0x35,
    "exceedNumberOfAttempts": 0x36,
    "requiredTimeDelayNotExpired": 0x37,
    "requestCorrectlyReceivedResponsePending": 0x78,
    "serviceNotSupportedInActiveSession": 0x7F,
}

# UDS 配置
UDSConfiguration = {
    "response_timeout_ms": 3000,
    "use_canfd": 0,
    "canfd_brs": 0,
    "trans_ver": 0,
    "fill_byte": 0x00,
    "frame_type": 0,
    "trans_stmin_valid": 0,
    "trans_stmin": 0,
    "enhanced_timeout_ms": 5000,
    "fc_timeout_ms": 1000,
    "fill_mode": 1,
}


# ==========================================
# 诊断日志格式化生成器（去除了 ASCII 预览）
# ==========================================
class CustomFileLogger:
    def __init__(self):
        self.log_file = None
        self.request_count = 0
        self.file_path = os.path.dirname(os.path.abspath(__file__))

    def start_log(self):
        """初始化日志文件"""
        log_dir = os.path.join(self.file_path, "logs")
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.log_file = os.path.join(log_dir, f"can_diagnostic_{timestamp}.log")
        self.request_count = 0

    def get_timestamp_str(self):
        """获取类似 [17:59:26.953] 的时间戳"""
        return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def write_line(self, line):
        """同时输出到 ZCANPRO 终端和本地文件"""
        zcanpro.write_log(line)
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

    def log_request_start(self, sid, data):
        """记录请求开始"""
        self.request_count += 1
        ts = self.get_timestamp_str()
        self.write_line(f"[{ts}] 第{self.request_count}个请求开始:")

        pdu_bytes = [sid] + list(data)
        pdu_hex = " ".join(f"{b:02X}" for b in pdu_bytes)
        pdu_len = f"{len(pdu_bytes):03d}"

        self.write_line(f"[{ts}] 请求数据, Len: {pdu_len}, PDU: {pdu_hex}")

    def log_response(self, response_data, expected_sid=None):
        """记录响应数据"""
        ts = self.get_timestamp_str()
        if not response_data:
            self.write_line(f"[{ts}] 响应数据为空")
            return

        pdu_len = len(response_data)
        pdu_hex = " ".join(f"{b:02X}" for b in response_data)

        # 判断是否为积极响应
        is_positive = False
        if expected_sid is not None and response_data[0] == expected_sid + 0x40:
            is_positive = True

        if is_positive:
            self.write_line(f"[{ts}] 积极响应, Len: {pdu_len:03d}, PDU: {pdu_hex} [校验响应] 回复与预期匹配")
        elif response_data[0] == 0x7F:
            self.write_line(f"[{ts}] 消极响应, Len: {pdu_len:03d}, PDU: {pdu_hex}")
        else:
            self.write_line(f"[{ts}] 响应数据, Len: {pdu_len:03d}, PDU: {pdu_hex}")

    def log_response_error(self, msg):
        """记录响应错误原因"""
        ts = self.get_timestamp_str()
        self.write_line(f"[{ts}] 请求错误! {msg}")

    def log_request_end(self):
        """记录请求结束"""
        ts = self.get_timestamp_str()
        self.write_line(f"[{ts}] 第{self.request_count}个请求结束")
        self.write_line("")  # 空行，与图片样式保持一致

    def log_delay(self, ms):
        """记录延时"""
        ts = self.get_timestamp_str()
        self.write_line(f"[{ts}] 延时{ms}毫秒...")


custom_logger = CustomFileLogger()

def custom_sleep(seconds):
    """延时控制，并记录到日志中"""
    global custom_logger
    ms = int(seconds * 1000)
    if ms <= 0:
        ms = 1
    custom_logger.log_delay(ms)
    time.sleep(seconds)


def call_zlgkey(seed_array, security_level, variant):
    """
    调用 ZLGKey 函数。
    """
    seed_array_c = (ctypes.c_ubyte * len(seed_array))(*seed_array)  # 转换为 ctypes 数组
    seed_array_size = ctypes.c_ushort(len(seed_array))             # 种子数组大小
    security_level_c = ctypes.c_uint(security_level)               # 安全级别
    variant_c = ctypes.c_char_p(variant.encode('utf-8'))           # 变体名称

    key_array = (ctypes.c_ubyte * seedLen)()                            # 密钥数组大小为 seedLen
    key_array_size = ctypes.c_ushort(seedLen)                           # 密钥数组大小

    result = dll.ZLGKey(
        seed_array_c,
        seed_array_size,
        security_level_c,
        variant_c,
        key_array,
        ctypes.byref(key_array_size)
    )

    if result != 0:
        raise RuntimeError(f"ZLGKey failed with error code: {result}")

    return list(key_array[:key_array_size.value])

def log_request(request):
    """
    记录请求日志。
    """
    zcanpro.write_log(
        f"[UDS Tx] {request['src_addr']:03X}\t{request['sid']:02X} "
        + " ".join(f"{byte:02X}" for byte in request["data"])
    )

def handle_response(response, request):
    """
    处理 UDS 响应。
    """
    if not response["result"]:
        zcanpro.write_log(f"Request error! {response['result_msg']}")
    elif response["data"][0] == request["sid"] + 0x40:
        zcanpro.write_log(
            f"[UDS Rx] {request['dst_addr']:03X}\t"
            + " ".join(f"{byte:02X}" for byte in response["data"])
            + "\tPositive Response"
        )
    else:
        zcanpro.write_log(
            f"[UDS Rx] {request['dst_addr']:03X}\t"
            + " ".join(f"{byte:02X}" for byte in response["data"])
        )

def convert_config(input_file, output_file, data_source=None):
    """
    data_source: 可选，用于提供实际数据的字典。若未提供，则所有数值填充为0。
    """
    with open(input_file, 'r', encoding='utf-8') as f:
        config = json.load(f)

    def get_value(path, default=0):
        if data_source is None:
            return default
        parts = path.split('.')
        val = data_source
        try:
            for p in parts:
                val = val[p]
            return val
        except (KeyError, TypeError):
            return default

    def traverse(node, path=""):
        if isinstance(node, dict):
            if "Length" in node:
                length = node["Length"]
                single_len = node.get("SingleLength", length)
                elem_count = length // single_len if single_len != 0 else 1
                data = get_value(path, [0] * elem_count)
                if isinstance(data, list) and len(data) != elem_count:
                    data = data[:elem_count] + [0] * (elem_count - len(data))
                return data
            else:
                return {k: traverse(v, path + "." + k if path else k) for k, v in node.items()}
        elif isinstance(node, list):
            return [traverse(item, path) for item in node]
        else:
            length = node
            data = get_value(path, 0)
            return data

    result = traverse(config)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

def truncate_str(s, max_len=200):
    """将字符串截断到指定长度，超长部分用 '...' 代替"""
    s = str(s)
    return s if len(s) <= max_len else s[:max_len] + "..."

def data_record(data):
    """
    解析并记录数据。
    """
    try:
        file_path = os.path.dirname(__file__)
        cfg_name = r'\config_data_type.json'
        with open(file_path + cfg_name, 'r') as cfg:
            config = json.load(cfg)
        algo_inter_var = parse_data(data, config)
        for k, v in algo_inter_var.items():
            zcanpro.write_log(f"{k}: {truncate_str(v)}")
        save_data(file_path, data, algo_inter_var)
    except Exception as e:
        zcanpro.write_log(f"DataRecord Error: {str(e)}")


def parse_data(data, config):
    """
    解析数据。
    """
    algo_inter_var = {}
    global project_name  # 声明使用全局变量

    # 根据数据类型解析
    if data[2] in [0x16, 0x17]:
        algo_inter_var = parse_crash_data(data, config[project_name]["FrontCrash"])
    elif data[2] in [0x18, 0x19]:
        # 1. 动态计算 SideCrashLH 配置中 event-type 的偏移量
        lh_config = config[project_name]["SideCrashLH"]
        offset = 3  # 数据起始偏移量
        event_type_offset = None

        for key, value in lh_config.items():
            if key == "algorithm_intermediate_variable":
                continue
            if "event-type" in key:
                event_type_offset = offset
                break

            if isinstance(value, int):
                offset += value
            else:
                offset += value["Length"]

        # 2. 读取 event-type 字段的值（1字节，无符号）
        event_type_val = None
        if event_type_offset is not None and event_type_offset < len(data):
            event_type_val = data[event_type_offset]

        # 3. 根据 event-type 值选择对应的配置进行解析
        if event_type_val == 4:
            # event-type 为 4，选择右侧配置
            algo_inter_var = parse_crash_data(data, config[project_name]["SideCrashRH"])
        else:
            # 默认为 3（或其它情况），选择左侧配置
            algo_inter_var = parse_crash_data(data, lh_config)
    else:
        zcanpro.write_log(f"Unknown data type: {data[2]:02X}")
        raise ValueError("Unknown data type for parsing crash data.")

    return algo_inter_var

def parse_crash_data(data, crash_config, start_offset = 3, byteorder='little'):
    """
    根据配置解析碰撞数据
    """
    if len(data) != crash_config["algorithm_intermediate_variable"] + 3:
        zcanpro.write_log(f"Wrong Length: {len(data)}, Expected: {crash_config['algorithm_intermediate_variable'] + 3}")
        raise ValueError("Data length is insufficient for parsing crash data.")
    if isinstance(data, list):
        data = bytes(data)
    offset = start_offset
    result = {}

    # 常规数值转换函数（保持纯净解析，有符号数 0xFFFF 正常输出为 -1）
    def bytes_to_int_normal(start, length, signed):
        if length == 1:
            return data[start]
        elif length == 2:
            raw = bytes([data[start + 1], data[start]])
            return int.from_bytes(raw, byteorder='big', signed=signed)
        elif length == 4:
            raw = bytes([data[start + 1], data[start], data[start + 3], data[start + 2]])
            return int.from_bytes(raw, byteorder='big', signed=signed)
        else:
            return int.from_bytes(data[start:start + length], byteorder='big', signed=signed)

    for key, value in crash_config.items():
        if key == "algorithm_intermediate_variable":
            continue

        if isinstance(value, int):
            length = value
            single_len = length
            signed = False
        else:
            length = value["Length"]
            single_len = value.get("SingleLength", length)
            signed = value.get("Signed", False)

        if offset + length > len(data):
            raise ValueError(f"Insufficient data for field '{key}': need {length} bytes at offset {offset}")

        # 取出该字段对应的完整原始字节
        raw_field_bytes = data[offset : offset + length]

        # 判断当前字段是否为数组类型
        is_array = single_len < length

        # 【精细化逻辑判断】：
        # 1. 数组整体判断：如果该数组的所有字节全部为 0xFF，说明整段数据未初始化（Flash 空白状态），直接整个字段返回单个 None (null)。
        if is_array and len(raw_field_bytes) > 0 and all(b == 0xFF for b in raw_field_bytes):
            result[key] = None
        else:
            # 2. 正常解析
            if is_array:
                elem_count = length // single_len
                arr = []
                for i in range(elem_count):
                    elem_start = offset + i * single_len
                    elem_raw = data[elem_start : elem_start + single_len]

                    # 只有无符号数的 0xFF... 才会视作无效并解析为 None。有符号数正常解析（0xFFFF 有符号下为 -1）
                    if all(b == 0xFF for b in elem_raw) and not signed:
                        val = None
                    else:
                        val = bytes_to_int_normal(elem_start, single_len, signed)
                    arr.append(val)
                result[key] = arr
            else:
                # 标量单值字段：只有无符号数且原始字节全部为 0xFF时才转换为 None
                if all(b == 0xFF for b in raw_field_bytes) and not signed:
                    result[key] = None
                else:
                    result[key] = bytes_to_int_normal(offset, length, signed)

        offset += length

    return result

def parse_side_crash_data(data, crash_config):
    """
    解析碰撞数据 (保留作为兼容或备用)。
    """
    if len(data) != crash_config["algorithm_intermediate_variable"] + 3:
        zcanpro.write_log(f"Wrong Length: {len(data)}")
        raise ValueError("Data length is insufficient for parsing crash data.")

    algo_inter_var = {}
    start_position = 1603
    for key, value in crash_config.items():
        length = value if isinstance(value, int) else value["Length"]
        algo_inter_var[key] = extract_data(data, start_position, length)
        start_position += length

    # 提取 x_acc_data
    x_acc_data = []
    try:
        for index in range(3, 403, 2):
            x_acc_data.append(int.from_bytes([data[index + 1], data[index]], byteorder='big', signed=True))
        algo_inter_var["x_acc_variable"] = x_acc_data
    except IndexError as e:
        algo_inter_var["x_acc_variable"] = None
        zcanpro.write_log(f"Error parsing x_acc_data: {str(e)}")

    # 提取 y_acc_data
    y_acc_data = []
    try:
        for index in range(403, 803, 2):
            y_acc_data.append(int.from_bytes([data[index + 1], data[index]], byteorder='big', signed=True))
        algo_inter_var["y_acc_variable"] = y_acc_data
    except IndexError as e:
        algo_inter_var["y_acc_variable"] = None
        zcanpro.write_log(f"Error parsing y_acc_data: {str(e)}")

    # 提取 outlay_acc
    outlay_acc = []
    try:
        for index in range(803, 1203, 2):
            outlay_acc.append(int.from_bytes([data[index + 1], data[index]], byteorder='big', signed=True))
        algo_inter_var["outlay_acc_variable"] = outlay_acc
    except IndexError as e:
        algo_inter_var["outlay_acc_variable"] = None
        zcanpro.write_log(f"Error parsing outlay_acc_variable: {str(e)}")

    # 提取 pressure_drop_signal_variable
    pressure_drop_signal = []
    try:
        for index in range(1203, 1603, 2):
            pressure_drop_signal.append(int.from_bytes([data[index + 1], data[index]], byteorder='big', signed=True))
        algo_inter_var["pressure_drop_signal_variable"] = pressure_drop_signal
    except IndexError as e:
        algo_inter_var["pressure_drop_signal_variable"] = None
        zcanpro.write_log(f"Error parsing pressure_drop_signal_variable: {str(e)}")

    return algo_inter_var

def extract_data(data, start, length):
    """
    单项提取（恢复常规行为，不对单一 0xFF/0xFFFF 强制转 None）
    """
    if length == 1:
        return data[start]
    elif length == 2:
        return int.from_bytes([data[start + 1], data[start]], byteorder='big', signed=True)
    elif length == 4:
        return int.from_bytes([data[start + 1], data[start], data[start + 3], data[start + 2]], byteorder='big', signed=True)
    return None

def save_data(file_path, data, algo_inter_var):
    """
    保存解析后的数据到文件。
    """
    data_dir = os.path.join(file_path, "data")
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)

    f_name = f"\\{data[1]:02X}{data[2]:02X}-{time.strftime('%Y-%m-%d_%H_%M_%S')}.json"
    full_path = os.path.join(data_dir, f_name.lstrip('\\'))

    with open(full_path, 'w') as f:
        json.dump(algo_inter_var, f, indent=4, separators=(',', ': '))

    zcanpro.write_log(f"Data saved to {full_path}")

def security_access_service_request_seed(bus_id, data):
    """
    请求安全访问种子。
    """
    global custom_logger
    custom_logger.log_request_start(UDSSID["SecurityAccessservice"], data)

    zcanpro.uds_init(UDSConfiguration)
    request = {
        "src_addr": DiagnosticAddressing["PhysicalAddressing"],
        "dst_addr": DiagnosticAddressing["ResponseAddressing"],
        "suppress_response": 0,
        "sid": UDSSID["SecurityAccessservice"],
        "data": data,
    }
    if not stopTask:
        log_request(request)
        response = zcanpro.uds_request(bus_id, request)
        if not response["result"]:
            zcanpro.write_log(f"Request error! {response['result_msg']}")
            custom_logger.log_response_error(response["result_msg"])
        else:
            global seed
            length = len(response["data"])
            seed = [0x00] * length
            seed = response["data"][2:]
            handle_response(response, request)
            custom_logger.log_response(response["data"], UDSSID["SecurityAccessservice"])

    custom_logger.log_request_end()
    zcanpro.uds_deinit()

def security_access_service_send_key(bus_id, data):
    """
    安全访问服务 - 发送密钥。
    """
    zcanpro.uds_init(UDSConfiguration)
    req_security_access_service_send_key = {
        "src_addr": DiagnosticAddressing["PhysicalAddressing"],
        "dst_addr": DiagnosticAddressing["ResponseAddressing"],
        "suppress_response": 0,
        "sid": UDSSID["SecurityAccessservice"],
        "data": data + [0] * seedLen
    }
    dll.ZLGKey.argtypes = [
        ctypes.POINTER(ctypes.c_ubyte),  # iSeedArray
        ctypes.c_ushort,                # iSeedArraySize
        ctypes.c_uint,                  # iSecurityLevel
        ctypes.c_char_p,                # iVariant
        ctypes.POINTER(ctypes.c_ubyte), # iKeyArray
        ctypes.POINTER(ctypes.c_ushort) # iKeyArraySize
    ]
    dll.ZLGKey.restype = ctypes.c_int

    global seed
    seed_parameters = (ctypes.c_ubyte * len(seed))(*seed)
    key_parameters = (ctypes.c_ubyte * len(seed))()
    level = req_security_access_service_send_key["data"][0] - 1
    variant = "default"

    key_parameters = call_zlgkey(seed_parameters, level, variant)

    for i in range(len(seed)):
        req_security_access_service_send_key["data"][i + 1] = key_parameters[i]

    global custom_logger
    custom_logger.log_request_start(UDSSID["SecurityAccessservice"], req_security_access_service_send_key["data"])

    if not stopTask:
        log_request(req_security_access_service_send_key)
        response = zcanpro.uds_request(bus_id, req_security_access_service_send_key)
        handle_response(response, req_security_access_service_send_key)

        if response["result"]:
            custom_logger.log_response(response["data"], UDSSID["SecurityAccessservice"])
        else:
            custom_logger.log_response_error(response["result_msg"])

    custom_logger.log_request_end()
    zcanpro.uds_deinit()

def uds_request(busID, UDSSID_val, data):
    global custom_logger
    custom_logger.log_request_start(UDSSID_val, data)

    zcanpro.uds_init(UDSConfiguration)
    req = {
        "src_addr": DiagnosticAddressing["PhysicalAddressing"],
        "dst_addr": DiagnosticAddressing["ResponseAddressing"],
        "suppress_response": 0,
        "sid": UDSSID_val,
        "data": data
    }

    zcanpro.write_log(
        "[UDS Tx] " + str('{:03X}'.format(req["src_addr"])) + '\t' + ("%02X " % req["sid"]) + " ".join(
            '{:02X}'.format(a) for a in req["data"]))
    response = zcanpro.uds_request(busID, req)
    if not response["result"]:
        zcanpro.write_log("Request error! " + response["result_msg"] + '\t' + "Not OK")
        custom_logger.log_response_error(response["result_msg"])
    elif response["data"][0] == req["sid"] + 0x40:
        zcanpro.write_log("[UDS Rx] " + str('{:03X}'.format(req["dst_addr"])) + '\t' + " ".join(
            '{:02X}'.format(a) for a in response["data"]) + '\t' + "Positive Response")
        custom_logger.log_response(response["data"], UDSSID_val)
    elif response["data"][0] == 0x7f:
        zcanpro.write_log("[UDS Rx] " + str('{:03X}'.format(req["dst_addr"])) + '\t' + " ".join(
            '{:02X}'.format(a) for a in response["data"]) + '\t' + "Negative Response")
        custom_logger.log_response(response["data"], UDSSID_val)
    else:
        zcanpro.write_log("[UDS Rx] " + str('{:03X}'.format(req["dst_addr"])) + '\t' + " ".join(
            '{:02X}'.format(a) for a in response["data"]) + '\t' + "Others")
        custom_logger.log_response(response["data"], UDSSID_val)

    custom_logger.log_request_end()
    zcanpro.uds_deinit()
    return response["data"]

def z_notify(type, obj):
    zcanpro.write_log("Notify " + str(type) + " " + str(obj))
    if type == "stop":
        zcanpro.write_log("Stop...")
        global stopTask
        stopTask = True

def z_main():
    """
    脚本主入口。
    """
    global custom_logger
    custom_logger.start_log()  # 初始化诊断文件

    buses = zcanpro.get_buses()
    zcanpro.write_log(f"Get buses: {buses}")
    start_time = time.time()

    # 1. 切换会话
    uds_request(buses[0]["busID"], UDSSID["DiagnosticSessionControlService"], [0x03])
    custom_sleep(0.001)

    # 2. 安全访问 - 请求 Seed
    security_access_service_request_seed(buses[0]["busID"], [0x01])
    custom_sleep(0.001)

    # 3. 安全访问 - 发送 Key
    security_access_service_send_key(buses[0]["busID"], [0x02])
    custom_sleep(0.001)

    # 4. 写入特定 Identifier
    uds_request(buses[0]["busID"], UDSSID["WriteDataByIdentifierservice"], [0x02, 0x33, 0x01, 0x55])
    custom_sleep(0.001)

    # 5. 读取数据 22 02 16
    data_16 = uds_request(buses[0]["busID"], 0x22, [0x02, 0x16])
    data_record(data_16)
    custom_sleep(timeDelaySec)

    # 6. 读取数据 22 02 17
    data_17 = uds_request(buses[0]["busID"], 0x22, [0x02, 0x17])
    data_record(data_17)
    custom_sleep(timeDelaySec)

    # 7. 读取数据 22 02 18
    data_18 = uds_request(buses[0]["busID"], 0x22, [0x02, 0x18])
    data_record(data_18)
    custom_sleep(timeDelaySec)

    # 8. 读取数据 22 02 19
    data_19 = uds_request(buses[0]["busID"], 0x22, [0x02, 0x19])
    data_record(data_19)

    run_times = (time.time() - start_time)
    zcanpro.write_log('运行时间%s' % run_times)

def generate_random_array(length):
    return [random.randint(0, 255) for _ in range(length)]