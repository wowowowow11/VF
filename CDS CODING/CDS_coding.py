import os
import sys
import time
import threading
import queue
import ctypes
import logging
import yaml
from datetime import datetime
from zlgcan import *

# 获取当前脚本所在的绝对路径，确保在不同运行环境下都能自适应
current_dir = os.path.dirname(os.path.abspath(__file__))

# 配置相对路径：在 py 脚本同目录下创建 log 文件夹
LOG_DIR = os.path.join(current_dir, "log")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR, exist_ok=True)

log_filename = os.path.join(LOG_DIR, f"CDS_Coding_Test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")


class GTestFormatter(logging.Formatter):
    """自定义日志格式，使其符合 GTest 输出和目标 TX/RX 格式"""

    def format(self, record):
        msg = str(record.msg)

        # 1. 提取消息开头的换行符 (解决日志空行问题)
        prefix = ""
        while msg.startswith("\n"):
            prefix += "\n"
            msg = msg[1:]

        # 2. 遇到 GTest 框架标签时，不加时间戳，但保留换行符
        if msg.startswith(("[==========]", "[ RUN      ]", "[       OK ]", "[  FAILED  ]", "[  PASSED  ]")):
            return prefix + msg

        # 3. 普通底层日志输出：2026/04/01-15:52:25.085187 INFO:xxx
        dt = datetime.fromtimestamp(record.created)
        time_str = dt.strftime('%Y/%m/%d-%H:%M:%S.%f')
        level = record.levelname

        return f"{prefix}{time_str} {level}:{msg}"


logger = logging.getLogger("DID_Test")
logger.setLevel(logging.INFO)
formatter = GTestFormatter()

file_handler = logging.FileHandler(log_filename, encoding='utf-8')
file_handler.setFormatter(formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)


def log(msg, level=logging.INFO):
    """全局日志打印函数"""
    if level == logging.INFO:
        logger.info(msg)
    elif level == logging.ERROR:
        logger.error(msg)
    # 强制刷新缓存
    for handler in logger.handlers:
        handler.flush()


# ================= 基础配置 =================
DEVICE_TYPE = ZCAN_USBCANFD_200U  # 设备类型
TARGET_PORT = 0  # 目标通道 (0 代表 Port 1)

# UDS 诊断 ID 配置
UDS_REQ_ID = 0x688  # 物理寻址请求 ID
UDS_RESP_ID = 0x608  # ECU 响应 ID

# 全局变量
thread_flag = True
uds_queue = queue.Queue()
project_name = "VF6.PY"


# ================= 格式化打印辅助函数 =================
def print_hex_data(prefix, data_list, wrap_len=16):
    """格式化输出十六进制数据，过长时自动换行，对齐显示"""
    if not data_list:
        log(f"{prefix} (空)")
        return

    if len(data_list) <= wrap_len:
        hex_str = " ".join([f"{b:02X}" for b in data_list])
        log(f"{prefix} {hex_str}")
    else:
        log(f"{prefix}")
        for i in range(0, len(data_list), wrap_len):
            chunk = data_list[i:i + wrap_len]
            hex_str = " ".join([f"{b:02X}" for b in chunk])
            log(f"    {hex_str}")


# ================= 智能混合解码函数 =================
def decode_did_data(actual_data):
    """
    智能解析诊断数据（混合 ASCII 与 Hex）：
    1. 如果数据完全由可打印的 ASCII 字符（0x20-0x7E）组成，直接以 ASCII 形式解码输出。
    2. 如果包含非打印字符，则采用混合解码：
       - 大写字母（A-Z，即 0x41 到 0x5A）解码为对应字符。
       - 其他所有字节解码为其两位十六进制字符串形式（如 0x16 -> "16", 0x74 -> "74"）。
    """
    if not actual_data:
        return ""

    is_all_printable = all(0x20 <= b <= 0x7E for b in actual_data)

    if is_all_printable:
        try:
            decoded = bytes(actual_data).decode('utf-8', errors='ignore').strip()
            return ''.join(c for c in decoded if c.isprintable())
        except Exception:
            pass

    # 混合模式解码（常用于汽车 BCD 码与大写英文字母组成的版本/零件号）
    result = []
    for b in actual_data:
        if 0x41 <= b <= 0x5A:  # 仅大写英文字母 A-Z 转换为 ASCII 字符
            result.append(chr(b))
        else:
            result.append(f"{b:02X}")
    return "".join(result)


# ================= 加载安全算法库 =================
ALGO_DLL_PATH = os.path.join(current_dir, "VF65ZLGDll.dll")

try:
    if sys.version_info >= (3, 8):
        os.add_dll_directory(current_dir)
    dll = ctypes.CDLL(ALGO_DLL_PATH)
    log(f" 安全算法库加载成功: {ALGO_DLL_PATH}")
except Exception as e:
    log(f" 安全算法库加载失败: {e}", logging.ERROR)
    dll = None


def call_zlgkey(seed_array, security_level, variant):
    if dll is None:
        raise RuntimeError("算法库未成功加载！")

    seed_array_c = (ctypes.c_ubyte * len(seed_array))(*seed_array)
    seed_array_size = ctypes.c_ushort(len(seed_array))
    security_level_c = ctypes.c_uint(security_level)
    variant_c = ctypes.c_char_p(variant.encode('utf-8'))

    key_array = (ctypes.c_ubyte * 16)()
    key_array_size = ctypes.c_ushort(16)

    result = dll.ZLGKey(
        seed_array_c, seed_array_size, security_level_c, variant_c,
        key_array, ctypes.byref(key_array_size)
    )

    if result != 0:
        raise RuntimeError(f"ZLGKey failed with error code: {result}")

    return list(key_array[:key_array_size.value])


# ================= 后台接收线程 =================
def receive_thread_func(zcanlib, chn_handle):
    global thread_flag
    while thread_flag:
        rcv_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CAN)
        if rcv_num:
            read_cnt = min(rcv_num, 100)
            rcv_msg, actual_num = zcanlib.Receive(chn_handle, read_cnt, 50)
            for i in range(actual_num):
                frame = rcv_msg[i].frame
                can_id = frame.can_id & 0x1FFFFFFF
                dlc = frame.can_dlc
                data = [frame.data[j] for j in range(dlc)]

                if can_id == UDS_RESP_ID:
                    uds_queue.put(data)
        time.sleep(0.005)


# ================= UDS 底层与 ISO-TP 协议处理 =================
def send_uds_raw(zcanlib, chn_handle, req_id, payload_8bytes):
    msgs = (ZCAN_Transmit_Data * 1)()
    msgs[0].transmit_type = 0
    msgs[0].frame.can_id = req_id
    msgs[0].frame.can_dlc = 8
    for i in range(8):
        msgs[0].frame.data[i] = payload_8bytes[i]
    tx_hex = " ".join([f"{b:02X}" for b in payload_8bytes])
    log(f"[TX] ID: 0x{req_id:03X} | DATA: {tx_hex}")
    return zcanlib.Transmit(chn_handle, msgs, 1) == 1


def wait_uds_response(timeout=2.0):
    try:
        data = uds_queue.get(timeout=timeout)
        rx_hex = " ".join([f"{b:02X}" for b in data])
        log(f"[RX] ID: 0x{UDS_RESP_ID:03X} | DATA: {rx_hex}")
        return data
    except queue.Empty:
        return None


def parse_multi_frame_response(zcanlib, chn_handle, first_frame):
    if not first_frame: return None
    pci = first_frame[0] >> 4
    if pci != 0x1: return first_frame
    total_len = ((first_frame[0] & 0x0F) << 8) | first_frame[1]
    received_data = first_frame[2:]

    # 回复流控制帧 (FC)
    fc_payload = [0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
    send_uds_raw(zcanlib, chn_handle, UDS_REQ_ID, fc_payload)

    next_seq = 1
    start_time = time.time()
    while len(received_data) < total_len and (time.time() - start_time < 5.0):
        cont_frame = wait_uds_response(0.5)
        if not cont_frame: continue
        if (cont_frame[0] >> 4) == 0x2:
            received_data.extend(cont_frame[1:])
            next_seq = (next_seq + 1) % 16
    return received_data[:total_len]


def execute_service(zcanlib, chn_handle, service_name, payload):
    log(f"\n{'-' * 55}")
    log(f"---> 执行: {service_name} <---")
    log(f"{'-' * 55}")
    print_hex_data(" 请求 Payload:", payload)

    # 清空旧队列数据
    while not uds_queue.empty():
        try:
            uds_queue.get_nowait()
        except:
            break

    # 1. 发送逻辑 (区分单帧多帧)
    if len(payload) <= 7:
        can_data = [len(payload)] + payload
        while len(can_data) < 8: can_data.append(0x00)
        if not send_uds_raw(zcanlib, chn_handle, UDS_REQ_ID, can_data):
            return None
    else:
        total_len = len(payload)
        can_data = [0x10 | (total_len >> 8), total_len & 0xFF] + payload[:6]
        send_uds_raw(zcanlib, chn_handle, UDS_REQ_ID, can_data)

        fc = wait_uds_response(2.0)
        if not fc or (fc[0] >> 4) != 3:
            log(" 未收到 ECU 流控制帧(FC)", logging.ERROR)
            return None
        bs = fc[1]

        remain_payload = payload[6:]
        seq = 1
        bs_count = 0
        while remain_payload:
            chunk = remain_payload[:7]
            remain_payload = remain_payload[7:]
            cf_data = [0x20 | seq] + chunk
            while len(cf_data) < 8: cf_data.append(0x00)
            send_uds_raw(zcanlib, chn_handle, UDS_REQ_ID, cf_data)
            seq = (seq + 1) & 0x0F
            bs_count += 1
            if bs != 0 and bs_count >= bs and remain_payload:
                fc = wait_uds_response(2.0)
                if not fc or (fc[0] >> 4) != 3:
                    return None
                bs_count = 0
            time.sleep(0.01)

    # 2. 接收响应逻辑
    sid = payload[0]
    while True:
        resp = wait_uds_response(3.0)
        if not resp:
            log(" ECU 响应超时！", logging.ERROR)
            return None

        pci = resp[0] >> 4
        full_resp = None

        if pci == 0x0:
            full_resp = resp[1: 1 + (resp[0] & 0x0F)]
        elif pci == 0x1:
            full_resp = parse_multi_frame_response(zcanlib, chn_handle, resp)

        if full_resp:
            if full_resp[0] == 0x7F and full_resp[1] == sid:
                if full_resp[2] == 0x78:
                    log(" 收到 NRC 78 (响应挂起)，继续等待...")
                    continue
                else:
                    log(f" 收到否定响应 (NRC): 0x{full_resp[2]:02X}", logging.ERROR)
                    return None
            elif full_resp[0] == (sid + 0x40):
                print_hex_data(" 肯定响应:", full_resp)
                return full_resp
            else:
                print_hex_data(" 收到未知响应:", full_resp)
                return full_resp


# ================= 硬件底层周期报文 =================
def setup_background_messages(zcanlib, device_handle, chn):
    log("\n[前置条件] 正在将 DBC 周期报文注入底层自动发送队列...")
    zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/clear_auto_send", "0".encode("utf-8"))

    messages = [
        {"id": 0x20D, "data": [0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], "interval_ms": 20},
        {"id": 0x112, "data": [0x2D, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00], "interval_ms": 100},
        {"id": 0x40D, "data": [0x17, 0x00, 0x00, 0x00, 0x00, 0x07, 0xD0, 0x00], "interval_ms": 120}
    ]
    for i, msg in enumerate(messages):
        auto_can = ZCAN_AUTO_TRANSMIT_OBJ()
        memset(addressof(auto_can), 0, sizeof(auto_can))
        auto_can.index = i
        auto_can.enable = 1
        auto_can.interval = msg["interval_ms"]
        auto_can.obj.transmit_type = 0
        auto_can.obj.frame.can_id = msg["id"]
        auto_can.obj.frame.can_dlc = len(msg["data"])
        for j in range(auto_can.obj.frame.can_dlc):
            auto_can.obj.frame.data[j] = msg["data"][j]
        zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/auto_send", byref(auto_can))

    zcanlib.ZCAN_SetValue(device_handle, str(chn) + "/apply_auto_send", "0".encode("utf-8"))
    log(" 硬件后台定时报文发送已开启。")


# ================= 预测试：读取诊断信息 (F101, F103, F141, F143, F148, F188, F191, F194) =================
def read_pre_diagnostic_info(zcanlib, chn_handle):
    """
    在执行测试用例前统一读取 DIDs 信息。仅在开始前执行一次。
    """
    log("\n" + "=" * 60)
    log("           正在读取预测试 ECU 诊断信息 (DIDs)           ")
    log("=" * 60)

    dids = [
        ("System Supplier ECU Software Version (22 F1 94)", [0x22, 0xF1, 0x94]),
        ("Boot Loader Version number (22 F1 01)", [0x22, 0xF1, 0x01]),
        ("ECU Purchase Part Number (22 F1 03)", [0x22, 0xF1, 0x03]),
        ("Vehicle Manufacturer ECU Purchase Part Number Revision Level (22 F1 43)", [0x22, 0xF1, 0x43]),
        ("Vehicle Manufacturer ECU Software Number Data Identifier (22 F1 88)", [0x22, 0xF1, 0x88]),
        ("Vehicle Manufacturer ECU Software Part Number Revision Level (22 F1 48)", [0x22, 0xF1, 0x48]),
        ("Vehicle Manufacturer ECU Hardware Number Data Identifier (22 F1 91)", [0x22, 0xF1, 0x91]),
        ("Vehicle Manufacturer ECU Hardware Part Number Revision Level (22 F1 41)", [0x22, 0xF1, 0x41]),
    ]

    for name, payload in dids:
        time.sleep(0.15)
        resp = execute_service(zcanlib, chn_handle, name, payload)
        if resp:
            actual_data = resp[3:]  # 去除 62 F1 XX 正向响应前缀
            hex_data = ' '.join(f'{x:02X}' for x in actual_data)

            # 使用智能混合解码逻辑对原始数据进行高兼容性解析
            ascii_data = decode_did_data(actual_data)

            log(f" -> {name}:")
            log(f"    (ASCII): {ascii_data}")
            log(f"    (Hex)  : {hex_data}")
        else:
            log(f" -> {name}: 读取失败/超时！", logging.WARNING)

    log("=" * 60 + "\n")


# ================= 用例执行逻辑 =================
def run_single_case(zcanlib, chn_handle, variant_name, data_str, finger_str, expected_coding_state_str):
    """
    执行单个测试用例 (刷写并校验)
    返回 (是否通过: bool, 失败原因: str)
    """
    try:
        payload_2e_f1_08 = [int(x, 16) for x in data_str.strip().split()]
    except Exception as e:
        return False, f"数据格式解析错误 ({e})"

    # --- [步骤 1] 预热：进入扩展模式 & 关闭 DTC ---
    time.sleep(0.5)
    if execute_service(zcanlib, chn_handle, "Diagnostic Session - Extended (10 03)", [0x10, 0x03]) is None:
        return False, "进入扩展模式失败"
    time.sleep(0.5)
    if execute_service(zcanlib, chn_handle, "Control DTC - Off (85 02)", [0x85, 0x02]) is None:
        return False, "关闭 DTC 失败"
    time.sleep(0.5)
    if execute_service(zcanlib, chn_handle, "Diagnostic Session - Custom (10 41)", [0x10, 0x41]) is None:
        return False, "进入 Custom Session 失败"

    # --- [步骤 2] 安全解锁 (27 05 / 27 06) ---
    resp_27 = execute_service(zcanlib, chn_handle, "Security Access - Request Seed (27 05)", [0x27, 0x05])
    time.sleep(0.5)
    if resp_27 is None or len(resp_27) < 3:
        return False, "获取 Seed 失败"
    time.sleep(0.5)

    seed_bytes = resp_27[2:]
    try:
        key_bytes = call_zlgkey(seed_bytes, security_level=5, variant=project_name)
    except Exception as e:
        return False, f"Key 计算失败 ({e})"
    time.sleep(0.5)
    if execute_service(zcanlib, chn_handle, "Security Access - Send Key (27 06)", [0x27, 0x06] + key_bytes) is None:
        return False, "发送 Key 解锁被拒绝/超时"

    # ---[步骤 3] 写入指纹/日期 (2E F1 0A) - 从 YAML 的 Finger 字段读取 ---
    time.sleep(0.5)
    if finger_str:
        try:
            payload_f1_0a = [int(x, 16) for x in finger_str.strip().split()]
            log(" 正在从 YAML 载入对应的 [Finger] 指纹数据...")
        except Exception as e:
            return False, f"YAML 内 Finger 数据格式解析错误 ({e})"
    else:
        # 回退逻辑：如果没用这个字节，就写固定的默认值
        log(" [WARNING] YAML 中没有找到 [Finger] 字段，将采用默认指纹数据。", logging.WARNING)
        payload_f1_0a = [int(x, 16) for x in
                         "2E F1 0A 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01".split()]

    if execute_service(zcanlib, chn_handle, "Write Data (2E F1 0A)", payload_f1_0a) is None:
        log(" [WARNING] 写入指纹/日期 (2E F1 0A) 失败/返回负响应，继续执行后续流程。", logging.WARNING)

    # --- [步骤 4] 写入核心 Coding 数据 (从 YAML 读取) ---
    time.sleep(0.5)
    if execute_service(zcanlib, chn_handle, f"Write Coding (2E F1 08) - {variant_name}", payload_2e_f1_08) is None:
        log(" [WARNING] 写入核心 Coding 数据 (2E F1 08) 失败/返回负响应，继续执行后续流程。", logging.WARNING)

    # --- [步骤 5] 校验与收尾 ---
    time.sleep(0.5)

    # 执行 31 01 02 00 Routine Control，并获取其返回值
    routine_resp = execute_service(zcanlib, chn_handle, "Routine Control (31 01 02 00)", [0x31, 0x01, 0x02, 0x00])

    # 动态构建校验预期值
    if expected_coding_state_str and expected_coding_state_str.strip():
        try:
            expected_resp = [int(x, 16) for x in expected_coding_state_str.strip().split()]
            log(f" 正在从 YAML 载入对应的 [Routine_Control] 预期校验数据...")
        except Exception as e:
            return False, f"YAML 内 Routine_Control 数据格式解析错误 ({e})"
    else:
        # 如果未提供该字段，则回退到原逻辑校验 71 01 02 00 00 00 00 00
        expected_resp = [0x71, 0x01, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00]

    expected_str = " ".join([f"{b:02X}" for b in expected_resp])

    # 进行结果一致性校验
    if routine_resp != expected_resp:
        actual_str = " ".join([f"{b:02X}" for b in routine_resp]) if routine_resp else "无响应/超时/否定响应"
        log(f" [FAIL] Routine Control (31 01 02 00) 校验未通过！", logging.ERROR)
        log(f"   预期值: {expected_str}", logging.ERROR)
        log(f"   实际值: {actual_str}", logging.ERROR)
        return False, f"Routine Control 响应值不符合要求! 预期: {expected_str}, 实际: {actual_str}"
    else:
        log(f" [PASS] Routine Control (31 01 02 00) 校验成功，响应结果为 {expected_str}。")

    # 收尾辅助信息读取（仅作日志展示，不作为测试判定标准）
    time.sleep(0.5)
    execute_service(zcanlib, chn_handle, "Diagnostic Session - Default (10 01)", [0x10, 0x01])

    # 读取指纹 (22 F1 0A) 仅供日志查看
    time.sleep(0.5)
    log(" 正在读取指纹数据 (22 F1 0A)...")
    read_finger_resp = execute_service(zcanlib, chn_handle, "Read Finger (22 F1 0A)", [0x22, 0xF1, 0x0A])
    if read_finger_resp:
        actual_finger = read_finger_resp[3:]
        log(f" 当前指纹数据: {' '.join(f'{x:02X}' for x in actual_finger)}")

    # 读取回显 (22 F1 08) 仅供日志查看
    log(f"\n 正在读取 [{variant_name}] 的回显数据...")
    read_resp = execute_service(zcanlib, chn_handle, "Read Coding (22 F1 08)", [0x22, 0xF1, 0x08])
    if read_resp:
        actual_coding = read_resp[3:]
        log(f" 当前回显数据: {' '.join(f'{x:02X}' for x in actual_coding)}")

    # 恢复 DTC 和复位操作（仅执行动作，不影响判定结果）
    time.sleep(0.5)
    execute_service(zcanlib, chn_handle, "Diagnostic Session - Extended (10 03)", [0x10, 0x03])
    time.sleep(0.5)
    execute_service(zcanlib, chn_handle, "Control DTC - On (85 01)", [0x85, 0x01])
    time.sleep(0.5)
    execute_service(zcanlib, chn_handle, "ECU Reset - Hard Reset (11 01)", [0x11, 0x01])

    return True, "Success"


# ================= 主流程：CDS Coding 刷写验证 =================
def main():
    global thread_flag
    zcanlib = ZCAN()

    # 1. 初始化硬件
    log("正在打开 CAN 设备...")
    handle = zcanlib.OpenDevice(DEVICE_TYPE, 0, 0)
    if handle == INVALID_DEVICE_HANDLE:
        log(" 打开设备失败！", logging.ERROR)
        return

    zcanlib.ZCAN_SetValue(handle, str(TARGET_PORT) + "/canfd_abit_baud_rate", "500000".encode("utf-8"))
    zcanlib.ZCAN_SetValue(handle, str(TARGET_PORT) + "/initenal_resistance", "1".encode("utf-8"))

    chn_init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    chn_init_cfg.can_type = ZCAN_TYPE_CANFD
    chn_init_cfg.config.canfd.mode = 0
    chn_handle = zcanlib.InitCAN(handle, TARGET_PORT, chn_init_cfg)

    if zcanlib.StartCAN(chn_handle) != ZCAN_STATUS_OK:
        log(" 启动通道失败！", logging.ERROR)
        return

    rx_thread = threading.Thread(target=receive_thread_func, args=(zcanlib, chn_handle))
    rx_thread.daemon = True
    rx_thread.start()

    try:
        # 启动后台周期报文
        setup_background_messages(zcanlib, handle, TARGET_PORT)
        time.sleep(1.0)

        # 执行用例前，在此处进行预测试 DIDs 统一读取一次
        read_pre_diagnostic_info(zcanlib, chn_handle)

        # 2. 加载 YAML 配置文件
        yaml_name = "VF6_CASE.yaml"
        yaml_path = os.path.join(current_dir, "Initial_test.yaml", yaml_name)

        # 保留回退逻辑
        if not os.path.exists(yaml_path):
            yaml_path = os.path.join(current_dir, "Initial_test.yaml", "VF6N_CASE.yaml")

        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
                payload_list = config_data.get('payloads', [])
        except Exception as e:
            log(f" 加载 YAML 配置文件 ({yaml_path}) 失败: {e}", logging.ERROR)
            return

        if not payload_list:
            log(" YAML 文件中没有找到 payloads 配置！", logging.ERROR)
            return

        total_cases = len(payload_list)
        passed_cases = []
        failed_cases = []

        log(f"\n🚀 检测到 {total_cases} 组配置用例，准备开始循环刷写测试流程...")

        # [GTest] 报告头部
        log(f"[==========] Running {total_cases} tests from CDS Configuration.")

        # 3. 核心循环
        for index, item in enumerate(payload_list):
            variant_name = item.get('name', f"Config_{index + 1}")
            data_str = item.get('data', '')
            finger_str = item.get('Finger', '')
            check_coding_state_str = item.get('Routine_Control', '')

            # [GTest] 用例开始
            log(f"[ RUN      ] {variant_name}")

            # 调用单个测试用例流程
            success, reason = run_single_case(
                zcanlib, chn_handle, variant_name, data_str, finger_str, check_coding_state_str
            )

            # 记录执行结果
            if success:
                # [GTest] 用例通过
                log(f"[       OK ] {variant_name}")
                passed_cases.append(variant_name)
            else:
                # [GTest] 用例失败
                log(f"[  FAILED  ] {variant_name} - Reason: {reason}", logging.ERROR)
                failed_cases.append(variant_name)

            # 等待 ECU 恢复或重启
            wait_time = 5.0
            log(f" 休息时间，等待 ECU 状态释放/重启 ({wait_time}s)...")
            time.sleep(wait_time)

        # ================= 4. 测试报告输出 =================
        log("\n" + "" * 65)
        log(" 自动化刷写测试报告汇总")
        log("" * 65)
        log(f" 总测试用例数: {total_cases}")
        log(f" 通过用例数  : {len(passed_cases)}")
        log(f" 未通过用例数: {len(failed_cases)}")

        # [GTest] 报告尾部
        log(f"[==========] {total_cases} tests ran.")
        log(f"[  PASSED  ] {len(passed_cases)} tests.")

        if failed_cases:
            log(f"[  FAILED  ] {len(failed_cases)} tests, listed below:", logging.ERROR)
            for idx, fail_name in enumerate(failed_cases):
                log(f"[  FAILED  ] {fail_name}", logging.ERROR)
        else:
            log("\n🎉 所有配置刷写及校验均已通过！")
        log("" * 65 + "\n")

    except KeyboardInterrupt:
        log("\n用户手动终止程序", logging.ERROR)
    finally:
        log("\n--- 清理资源并关闭设备 ---")
        thread_flag = False
        rx_thread.join(timeout=1.0)
        zcanlib.ZCAN_SetValue(handle, str(TARGET_PORT) + "/clear_auto_send", "0".encode("utf-8"))
        zcanlib.ResetCAN(chn_handle)
        zcanlib.CloseDevice(handle)


if __name__ == "__main__":
    main()