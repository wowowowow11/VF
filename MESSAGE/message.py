import time
import threading
import queue
import json
import ctypes
import os
import logging
import traceback
import random
from datetime import datetime
from zlgcan import *

# 获取当前脚本所在的绝对路径，确保在不同运行环境下都能自适应
current_dir = os.path.dirname(os.path.abspath(__file__))

# ================= 1. 日志配置 (自适应相对路径) =================
LOG_DIR = os.path.join(current_dir, "log")
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR, exist_ok=True)

log_filename = os.path.join(LOG_DIR, f"Message_Test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
traffic_log_filename = os.path.join(LOG_DIR, f"CAN_Bus_Log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")


class GTestFormatter(logging.Formatter):
    """自定义日志格式，使其符合 GTest 输出和目标 TX/RX 格式"""

    def format(self, record):
        msg = str(record.msg)
        if msg.startswith(("[==========]", "[ RUN      ]", "[       OK ]", "[  FAILED  ]", "[  PASSED  ]")):
            return msg
        dt = datetime.fromtimestamp(record.created)
        time_str = dt.strftime('%Y/%m/%d-%H:%M:%S.%f')
        level = record.levelname
        return f"{time_str} {level}:{msg}"


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
    for handler in logger.handlers:
        handler.flush()


# ================= 1.2 高性能异步总线报文日志记录器 =================
traffic_counter = 0
traffic_lock = threading.Lock()
traffic_queue = queue.Queue()  # 异步日志队列


def init_traffic_log():
    """初始化总线报文日志，写入表头"""
    header = f"{'序号':<8}{'时间标识':<15}{'源通道':<8}{'帧ID':<10}{'帧类型':<10}{'帧格式':<10}{'CAN类型':<10}{'方向':<8}{'长度':<6}{'数据'}"
    with open(traffic_log_filename, "w", encoding="utf-8") as f:
        f.write(header + "\n")


def log_bus_traffic(direction, can_id, dlc, data, can_type="CAN", channel=0):
    """将单条报文格式化后放入内存队列，避免阻塞主收发线程"""
    global traffic_counter
    with traffic_lock:
        is_extended = bool(can_id & 0x80000000)
        is_remote = bool(can_id & 0x40000000)

        frame_id_val = can_id & 0x1FFFFFFF
        frame_type_str = "扩展帧" if is_extended else "标准帧"
        frame_format_str = "远程帧" if is_remote else "数据帧"

        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        id_hex = f"0x{frame_id_val:X}"
        data_hex = " ".join([f"{b:02X}" for b in data[:dlc]])

        row = f"{traffic_counter:<8}{timestamp:<15}{channel:<8}{id_hex:<10}{frame_type_str:<10}{frame_format_str:<10}{can_type:<10}{direction:<8}{dlc:<6}{data_hex}"
        traffic_queue.put(row)
        traffic_counter += 1


def traffic_writer_thread_func():
    """独立的日志写磁盘后台守护线程 (批量刷盘，保障主线程不卡顿)"""
    global thread_flag
    # 保持文件句柄打开，以提高写入性能
    with open(traffic_log_filename, "a", encoding="utf-8", buffering=8192) as f:
        while thread_flag or not traffic_queue.empty():
            try:
                row = traffic_queue.get(timeout=0.1)
                f.write(row + "\n")
            except queue.Empty:
                continue


# ================= 1.3 周期性发送报文模拟记录线程 =================
def periodic_tx_logger_func():
    """在后台按照周期时间模拟并记录由硬件发送的 Node 报文"""
    global thread_flag
    last_log_times = {}
    while thread_flag:
        now = time.time()
        nodes = list(dynamic_node_pool.values())
        for node in nodes:
            node_id = node["id"]
            if not node["active"]:
                if node_id in last_log_times:
                    del last_log_times[node_id]
                continue

            interval_sec = node["interval_ms"] / 1000.0
            if node_id not in last_log_times:
                last_log_times[node_id] = now
                log_bus_traffic("Tx", node["id"], len(node["data"]), node["data"], can_type="CAN", channel=TARGET_PORT)
                continue

            if now - last_log_times[node_id] >= interval_sec:
                log_bus_traffic("Tx", node["id"], len(node["data"]), node["data"], can_type="CAN", channel=TARGET_PORT)
                last_log_times[node_id] = now
        time.sleep(0.002)


# ================= 2. 基础配置 (保留 21 个 ID 的完整白名单) =================
DEVICE_TYPE = ZCAN_USBCANFD_200U
TARGET_PORT = 0
UDS_REQ_ID = 0x688
UDS_RESP_ID = 0x608

CRC_TEST_WHITELIST = [
    "D58083", "D58183", "D58283", "D58383", "D58483", "D58583",
    "D58683", "D58883", "D58983", "D58A83", "D58B83", "D58C83",
    "D58D83", "D58F83", "D59083", "D59183", "D59283", "D59583",
    "D59683", "D59983"
]

thread_flag = True
uds_queue = queue.Queue()
dynamic_node_pool = {}
test_summary_list = []


# ================= 3. 底层传输与接收 =================
def format_hex(data):
    return " ".join([f"{b:02X}" for b in data])


def receive_thread_func(zcanlib, chn_handle):
    global thread_flag
    while thread_flag:
        # 1. 接收标准 CAN 报文
        rcv_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CAN)
        if rcv_num:
            rcv_msg, actual_num = zcanlib.Receive(chn_handle, rcv_num, 50)
            for i in range(actual_num):
                frame = rcv_msg[i].frame
                raw_data = [frame.data[j] for j in range(frame.can_dlc)]
                log_bus_traffic("Rx", frame.can_id, frame.can_dlc, raw_data, can_type="CAN", channel=TARGET_PORT)

                if (frame.can_id & 0x1FFFFFFF) == UDS_RESP_ID:
                    uds_queue.put(raw_data)

        # 2. 接收 CANFD 报文
        rcv_num_fd = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CANFD)
        if rcv_num_fd:
            rcv_msg_fd, actual_num_fd = zcanlib.ReceiveFD(chn_handle, rcv_num_fd, 50)
            for i in range(actual_num_fd):
                frame = rcv_msg_fd[i].frame
                raw_data = [frame.data[j] for j in range(frame.len)]
                log_bus_traffic("Rx", frame.can_id, frame.len, raw_data, can_type="CANFD", channel=TARGET_PORT)

                if (frame.can_id & 0x1FFFFFFF) == UDS_RESP_ID:
                    uds_queue.put(raw_data)

        time.sleep(0.005)


def send_raw_uds(zcanlib, chn_handle, data_8bytes):
    """底层的单帧发送函数"""
    log(f"Tx  0x{UDS_REQ_ID:03X}  8  {format_hex(data_8bytes)}")
    log_bus_traffic("Tx", UDS_REQ_ID, 8, data_8bytes, can_type="CAN", channel=TARGET_PORT)

    msgs = (ZCAN_Transmit_Data * 1)()
    msgs[0].transmit_type = 0
    msgs[0].frame.can_id = UDS_REQ_ID
    msgs[0].frame.can_dlc = 8
    for i in range(8): msgs[0].frame.data[i] = data_8bytes[i]
    zcanlib.Transmit(chn_handle, msgs, 1)


def execute_service(zcanlib, chn_handle, service_name, payload):
    """UDS 状态机执行"""
    while not uds_queue.empty():
        try:
            uds_queue.get_nowait()
        except:
            break

    send_data = [len(payload)] + payload
    while len(send_data) < 8: send_data.append(0x00)

    send_raw_uds(zcanlib, chn_handle, send_data)

    full_payload = []
    start_time = time.time()

    while (time.time() - start_time) < 3.0:
        try:
            frame = uds_queue.get(timeout=1.0)
            pci_type = frame[0] >> 4

            if pci_type == 0x0:
                log(f"Rx  0x{UDS_RESP_ID:03X}  8  {format_hex(frame)}")
                full_payload = frame[1: 1 + (frame[0] & 0x0F)]
                break
            elif pci_type == 0x1:
                log(f"Rx  0x{UDS_RESP_ID:03X}  8  {format_hex(frame)}")
                total_len = ((frame[0] & 0x0F) << 8) | frame[1]
                full_payload = frame[2:]
                send_raw_uds(zcanlib, chn_handle, [0x30, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                while len(full_payload) < total_len:
                    try:
                        cf = uds_queue.get(timeout=1.0)
                        if (cf[0] >> 4) == 0x2:
                            log(f"Rx  0x{UDS_RESP_ID:03X}  8  {format_hex(cf)}")
                            full_payload.extend(cf[1:])
                    except queue.Empty:
                        break
                full_payload = full_payload[:total_len]
                break
        except queue.Empty:
            continue

    if service_name:
        log(service_name)

    if full_payload:
        if full_payload[0] == 0x7F:
            log("Negative response")

        log("The receiving task is complete.")
        log(f"UDS Request: Length {len(payload)}, PDU: {format_hex(payload)}")
        log(f"UDS Response: Length {len(full_payload)}, PDU: {format_hex(full_payload)}")

        if full_payload[0] == 0x7F and full_payload[2] == 0x78:
            time.sleep(0.5)
            return execute_service(zcanlib, chn_handle, service_name, payload)
        return full_payload
    else:
        log("No response received")
        log("The receiving task is complete.")
        log(f"UDS Request: Length {len(payload)}, PDU: {format_hex(payload)}")
        return None


# ================= 4. 业务封装 =================
def clear_dtcs(zcanlib, chn_handle):
    execute_service(zcanlib, chn_handle, "Clear Diagnostic Information", [0x14, 0xFF, 0xFF, 0xFF])
    time.sleep(0.5)


def read_dtcs(zcanlib, chn_handle):
    """读取并自动过滤屏蔽 DTCTest.json 以外的故障码"""
    resp = execute_service(zcanlib, chn_handle, "Read DTC Information", [0x19, 0x02, 0xFF])
    dtc_list = []

    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        dtc_json_path = os.path.join(script_dir, "VF6_425", "DTCTest.json")
        with open(dtc_json_path, 'r', encoding='utf-8') as f:
            dtcs_def = json.load(f)["DTCLists"][0]
            allowed_dtcs = {item["DTCNumber"].strip().upper() for item in dtcs_def if "DTCNumber" in item}
    except Exception as e:
        log(f"⚠️ [WARNING] 无法加载 DTCTest.json 进行故障码过滤: {e}", logging.WARNING)
        allowed_dtcs = set()

    if resp and resp[0] == 0x59:
        for i in range(3, len(resp) - 2, 4):
            dtc_id = f"{resp[i]:02X}{resp[i + 1]:02X}{resp[i + 2]:02X}"
            if dtc_id != "000000":
                if allowed_dtcs and (dtc_id.upper() not in allowed_dtcs):
                    continue
                dtc_list.append(dtc_id)

    log(f" 过滤后有效的测试集 DTC 列表 (Filtered DTCs): {dtc_list}")
    return dtc_list


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


# ================= 预测试：读取诊断信息 (F101, F103, F141, F143, F148, F188, F191, F194) =================
def read_pre_diagnostic_info(zcanlib, chn_handle):
    """
    在执行测试用例前统一读取 DIDs 信息。仅在开始前执行一次。
    """
    log("\n" + "=" * 60)
    log("           正在读取预测试 ECU 诊断信息 (DIDs)           ")
    log("=" * 60)

    # 已修复首项尾部漏掉逗号的语法错误
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


# ================= 5. 测试序列 =================
def apply_periodic_msgs(zcanlib, device_handle, chn):
    zcanlib.ZCAN_SetValue(device_handle, f"{chn}/clear_auto_send", "0".encode())
    for idx, node in dynamic_node_pool.items():
        if node["active"]:
            auto_can = ZCAN_AUTO_TRANSMIT_OBJ()
            ctypes.memset(ctypes.addressof(auto_can), 0, ctypes.sizeof(auto_can))
            auto_can.index = idx
            auto_can.enable = 1
            auto_can.interval = node["interval_ms"]
            auto_can.obj.frame.can_id = node["id"]
            auto_can.obj.frame.can_dlc = len(node["data"])
            for j in range(len(node["data"])): auto_can.obj.frame.data[j] = node["data"][j]
            zcanlib.ZCAN_SetValue(device_handle, f"{chn}/auto_send", ctypes.byref(auto_can))
    zcanlib.ZCAN_SetValue(device_handle, f"{chn}/apply_auto_send", "0".encode())


def run_main_test_loop(zcanlib, device_handle, chn_handle):
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        node_json_path = os.path.join(script_dir, "VF6_425", "NodeTest_base.json")
        dtc_json_path = os.path.join(script_dir, "VF6_425", "DTCTest.json")

        # 解析内部的节点列表
        with open(node_json_path, 'r', encoding='utf-8') as f:
            nodes_def = json.load(f)["MessagesList"][0]
        with open(dtc_json_path, 'r', encoding='utf-8') as f:
            dtcs_def = json.load(f)["DTCLists"][0]

        if len(nodes_def) == 0:
            log("JSON parsed, but MessagesList is empty!", level=logging.ERROR)
            return

        log(f" 测试启动. 日志路径: {log_filename}")
        log(f" 完整总线报文流记录路径: {traffic_log_filename}")

        # 解析并注入动态周期报文池
        for i, node in enumerate(nodes_def):
            dynamic_node_pool[i] = {
                "id": int(node["MessageID"], 16),
                "data": [int(x.strip(), 16) for x in node["data"].split(",")],
                "interval_ms": node["CycleTime"],
                "active": True, "name": node["MessageName"], "id_hex": node["MessageID"]
            }

        # 启动周期报文发送（加载 NodeTest_option.json 中的前置条件节点）
        apply_periodic_msgs(zcanlib, device_handle, TARGET_PORT)

        # 等待 2 秒让节点周期报文开始稳定在总线上发送，确保 ECU 维持唤醒且处于正常工作模式
        time.sleep(2)

        # 此时周期报文正在正常发送中，可以稳定执行 DIDs 的读取与解析了
        read_pre_diagnostic_info(zcanlib, chn_handle)

        # 1. 启动异步总线日志文件刷写线程
        writer_thread = threading.Thread(target=traffic_writer_thread_func)
        writer_thread.daemon = True
        writer_thread.start()

        # 2. 启动周期发送后台模拟记录线程
        tx_log_thread = threading.Thread(target=periodic_tx_logger_func)
        tx_log_thread.daemon = True
        tx_log_thread.start()

        active_tests_count = 0
        for idx, node in dynamic_node_pool.items():
            active_tests_count += 1
            id_hex = node["id_hex"]
            exp_c = next(
                (d["DTCNumber"] for d in dtcs_def if d["RelatedMessageID"] == id_hex and d["DTCType"] == "CRCError"),
                None)
            if exp_c and exp_c in CRC_TEST_WHITELIST:
                active_tests_count += 1

        log(f"[==========] Running {active_tests_count} test(s).")

        # 遍历节点进行测试
        for idx, node in dynamic_node_pool.items():
            name, id_hex = node["name"], node["id_hex"]
            exp_m = next((d["DTCNumber"] for d in dtcs_def if
                          d["RelatedMessageID"] == id_hex and d["DTCType"] == "MissingMessage"), None)
            exp_c = next(
                (d["DTCNumber"] for d in dtcs_def if d["RelatedMessageID"] == id_hex and d["DTCType"] == "CRCError"),
                None)

            log("\n" + "#" * 60 + f"\n## 正在测试节点: {name} (0x{id_hex})\n" + "#" * 60)

            # ================= 测试项 A: 丢失报文测试 (带最多重试2次机制) =================
            test_case_missing = f"Node0x{id_hex}-{name} - testMissingMessage"
            log(f"[ RUN      ] {test_case_missing}")

            res_m = "FAIL"
            max_attempts_m = 3  # 1次正常执行 + 最多重试2次
            for attempt in range(1, max_attempts_m + 1):
                if attempt > 1:
                    log(f"⚠️ [RETRY] {test_case_missing} 未通过，正在执行第 {attempt - 1} 次重试...")

                # 确保在正常通信状态下执行 10 03 切换并执行 14 清除 DTC
                execute_service(zcanlib, chn_handle, "Diagnostic Session Control", [0x10, 0x03])
                clear_dtcs(zcanlib, chn_handle)

                # 清除完毕后，再开始制造报文丢失故障
                log(f"Stopping TX for 0x{id_hex} (Attempt {attempt}/{max_attempts_m})")
                node["active"] = False
                apply_periodic_msgs(zcanlib, device_handle, TARGET_PORT)

                log("   等待 10 秒故障成熟...")
                time.sleep(10)

                found = read_dtcs(zcanlib, chn_handle)

                # 【新增匹配故障码的打印逻辑】
                if exp_m:
                    if exp_m in found:
                        log(f" 🎯 成功匹配当前节点预期的丢失故障码 (Expected DTC): [ {exp_m} ]")
                    else:
                        log(f" ❌ 未能在当前过滤后的 DTC 列表中找到预期的丢失故障码 (Expected DTC): [ {exp_m} ]",
                            logging.WARNING)

                # 恢复节点状态并等待自愈
                node["active"] = True
                apply_periodic_msgs(zcanlib, device_handle, TARGET_PORT)
                time.sleep(4)

                if exp_m in found:
                    res_m = "PASS"
                    log(f"[       OK ] {test_case_missing}")
                    break
                else:
                    res_m = "FAIL"
                    log(f"Expected DTC {exp_m} NOT found on attempt {attempt}/{max_attempts_m}. Actual (Filtered): {found}",
                        level=logging.ERROR)
                    # 只有最后一次重试都失败了，才写入 GTest FAILED 终结标记
                    if attempt == max_attempts_m:
                        log(f"[  FAILED  ] {test_case_missing}")

            test_summary_list.append(res_m)

            # ================= 测试项 B: CRC 测试 (带最多重试2次机制) =================
            test_case_crc = f"Node0x{id_hex}-{name} - testCRCError"
            if exp_c and exp_c in CRC_TEST_WHITELIST:
                log(f"[ RUN      ] {test_case_crc}")

                res_c = "FAIL"
                max_attempts_c = 3  # 1次正常执行 + 最多重试2次
                orig_data = node["data"].copy()

                for attempt in range(1, max_attempts_c + 1):
                    if attempt > 1:
                        log(f"⚠️ [RETRY] {test_case_crc} 未通过，正在执行第 {attempt - 1} 次重试...")

                    # 确保在正常通信状态下执行 10 03 切换并执行 14 清除 DTC
                    execute_service(zcanlib, chn_handle, "Diagnostic Session Control", [0x10, 0x03])
                    clear_dtcs(zcanlib, chn_handle)

                    # 首尾字节同步随机篡改机制（生成且不等于原值的随机数）
                    log(f"Modifying first and last byte for CRC Error (Attempt {attempt}/{max_attempts_c})")

                    # 1. 随机篡改首字节
                    r_first = random.randint(0, 255)
                    while r_first == orig_data[0]:
                        r_first = random.randint(0, 255)
                    node["data"][0] = r_first

                    # 2. 随机篡改尾字节（多于 1 个字节时执行独立尾字节篡改）
                    if len(orig_data) > 1:
                        r_last = random.randint(0, 255)
                        while r_last == orig_data[-1]:
                            r_last = random.randint(0, 255)
                        node["data"][-1] = r_last

                    log(f"  原始信号值 (Original Data): [ {format_hex(orig_data)} ]")
                    log(f"  篡改后信号值 (Tampered Data): [ {format_hex(node['data'])} ]")
                    apply_periodic_msgs(zcanlib, device_handle, TARGET_PORT)

                    log("   等待 5 秒故障成熟...")
                    time.sleep(5)

                    found = read_dtcs(zcanlib, chn_handle)

                    # 【新增匹配故障码的打印逻辑】
                    if exp_c:
                        if exp_c in found:
                            log(f" 🎯 成功匹配当前节点预期的 CRC 故障码 (Expected DTC): [ {exp_c} ]")
                        else:
                            log(f" ❌ 未能在当前过滤后的 DTC 列表中找到预期的 CRC 故障码 (Expected DTC): [ {exp_c} ]",
                                logging.WARNING)

                    # 恢复原始报文数据并等待自愈
                    node["data"] = orig_data
                    apply_periodic_msgs(zcanlib, device_handle, TARGET_PORT)
                    time.sleep(4)

                    if exp_c in found:
                        res_c = "PASS"
                        log(f"[       OK ] {test_case_crc}")
                        break
                    else:
                        res_c = "FAIL"
                        log(f"Expected DTC {exp_c} NOT found on attempt {attempt}/{max_attempts_c}. Actual (Filtered): {found}",
                            level=logging.ERROR)
                        # 只有最后一次重试也失败了，才写入 GTest FAILED 终结标记
                        if attempt == max_attempts_c:
                            log(f"[  FAILED  ] {test_case_crc}")

                test_summary_list.append(res_c)
            else:
                test_summary_list.append("SKIP")

        total_nodes = len(nodes_def)
        passed = test_summary_list.count("PASS")
        failed = test_summary_list.count("FAIL")
        skipped = test_summary_list.count("SKIP")
        total_exe = passed + failed
        rate = (passed / total_exe * 100) if total_exe > 0 else 0

        log("\n" + "=" * 50)
        log(" 所有节点测试完成 - 整体统计汇总")
        log("=" * 50)
        log(f"  完成时间:      {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"  测试节点总数:  {total_nodes} 个")
        log("-" * 50)
        log(f"  有效执行测试项: {total_exe:<4}")
        log(f"  成功 (PASS):    {passed:<4}")
        log(f"  失败 (FAIL):    {failed:<4}")
        log(f"  跳过 (SKIP):    {skipped:<4}")
        log("-" * 50)
        log(f"  整体通过率:     {rate:.2f}%")
        log("=" * 50)
        log(f"详细记录已保存至: {log_filename}\n")

        log(f"[==========] {total_exe} test(s) run.")
        if passed == total_exe:
            log(f"[  PASSED  ] {total_exe} test(s).")
        else:
            log(f"[  FAILED  ] {failed} test(s).")

    except Exception as e:
        log(f"[ RUN      ] ScriptExecution - FatalError")
        log(f"Exception raised: {str(e)}", level=logging.ERROR)
        log(traceback.format_exc(), level=logging.ERROR)
        log(f"[  FAILED  ] ScriptExecution - FatalError")


if __name__ == "__main__":
    init_traffic_log()

    zcanlib = ZCAN()
    device_handle = zcanlib.OpenDevice(DEVICE_TYPE, 0, 0)
    if device_handle == INVALID_DEVICE_HANDLE:
        print("Cannot Open ZLG Device!")
        exit()

    # 💡 【重要修复区域】
    # 将 message.py 里的硬件初始化代码更新，设置波特率和内部终端电阻
    zcanlib.ZCAN_SetValue(device_handle, str(TARGET_PORT) + "/canfd_abit_baud_rate", "500000".encode("utf-8"))
    zcanlib.ZCAN_SetValue(device_handle, str(TARGET_PORT) + "/initenal_resistance", "1".encode("utf-8"))

    init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    init_cfg.can_type = ZCAN_TYPE_CANFD
    init_cfg.config.canfd.mode = 0  # 正常工作模式
    chn_handle = zcanlib.InitCAN(device_handle, TARGET_PORT, init_cfg)
    zcanlib.StartCAN(chn_handle)

    t = threading.Thread(target=receive_thread_func, args=(zcanlib, chn_handle))
    t.daemon = True
    t.start()

    try:
        run_main_test_loop(zcanlib, device_handle, chn_handle)
    finally:
        thread_flag = False
        zcanlib.CloseDevice(device_handle)
        log("CloseDevice success.")