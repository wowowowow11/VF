import time
import threading
import queue
import json
import ctypes
import os
import logging
import traceback
from datetime import datetime
from zlgcan import *

# ================= 1. 日志配置 (定制化格式适配 outputReport) =================
LOG_DIR = r"F:\Python3.9.10\Win\DID_list\log"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR, exist_ok=True)

log_filename = os.path.join(LOG_DIR, f"DID_Test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")


class GTestFormatter(logging.Formatter):
    """自定义日志格式，使其完美符合 GTest 输出和目标 TX/RX 格式"""

    def format(self, record):
        msg = str(record.msg)
        # 1. 遇到 GTest 框架标签时，不加时间戳
        if msg.startswith(("[==========]", "[ RUN      ]", "[       OK ]", "[  FAILED  ]", "[  PASSED  ]")):
            return msg
        # 2. 普通底层日志，按照目标格式输出：2026/04/01-15:52:25.085187 INFO:xxx
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
    # 强制刷新缓存
    for handler in logger.handlers:
        handler.flush()


# ================= 2. 基础配置 =================
DEVICE_TYPE = ZCAN_USBCANFD_200U
TARGET_PORT = 0
UDS_REQ_ID = 0x688
UDS_RESP_ID = 0x608

thread_flag = True
uds_queue = queue.Queue()
dynamic_node_pool = {}

# 统计用变量
test_summary_list = []
failed_tests_list = []  # 新增：用于记录失败的用例名称


# ================= 3. 底层传输与接收 =================
def format_hex(data):
    return " ".join([f"{b:02X}" for b in data])


def receive_thread_func(zcanlib, chn_handle):
    global thread_flag
    while thread_flag:
        rcv_num = zcanlib.GetReceiveNum(chn_handle, ZCAN_TYPE_CAN)
        if rcv_num:
            rcv_msg, actual_num = zcanlib.Receive(chn_handle, rcv_num, 50)
            for i in range(actual_num):
                frame = rcv_msg[i].frame
                if (frame.can_id & 0x1FFFFFFF) == UDS_RESP_ID:
                    uds_queue.put([frame.data[j] for j in range(frame.can_dlc)])
        time.sleep(0.005)


def send_raw_uds(zcanlib, chn_handle, data_8bytes):
    """底层发送函数，日志样式修改为目标格式：Tx  0x688  8  02 10 01..."""
    log(f"Tx  0x{UDS_REQ_ID:03X}  8  {format_hex(data_8bytes)}")
    msgs = (ZCAN_Transmit_Data * 1)()
    msgs[0].transmit_type = 0
    msgs[0].frame.can_id = UDS_REQ_ID
    msgs[0].frame.can_dlc = 8
    for i in range(8): msgs[0].frame.data[i] = data_8bytes[i]
    zcanlib.Transmit(chn_handle, msgs, 1)


def execute_service(zcanlib, chn_handle, service_name, payload):
    """UDS 状态机执行，日志样式严格仿照目标格式"""
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

    # ==== 打印目标格式的 UDS 解析日志 ====
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
    resp = execute_service(zcanlib, chn_handle, "Read DTC Information", [0x19, 0x02, 0xFF])
    dtc_list = []
    if resp and resp[0] == 0x59:
        for i in range(3, len(resp) - 2, 4):
            dtc_id = f"{resp[i]:02X}{resp[i + 1]:02X}{resp[i + 2]:02X}"
            if dtc_id != "000000": dtc_list.append(dtc_id)
    return dtc_list


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
        with open('VFWILD_511/NodeTest_base.json', 'r', encoding='utf-8') as f:
            nodes_def = json.load(f)["MessagesList"]
        with open('VFWILD_511/DTCTest.json', 'r', encoding='utf-8') as f:
            dtcs_def = json.load(f)["DTCLists"][0]

        if len(nodes_def) == 0:
            log("JSON parsed, but MessagesList is empty!", level=logging.ERROR)
            return

        for i, node in enumerate(nodes_def):
            dynamic_node_pool[i] = {
                "id": int(node["MessageID"], 16),
                "data": [int(x.strip(), 16) for x in node["data"].split(",")],
                "interval_ms": node["CycleTime"],
                "active": True, "name": node["MessageName"], "id_hex": node["MessageID"]
            }
        apply_periodic_msgs(zcanlib, device_handle, TARGET_PORT)
        time.sleep(2)

        # 计算将要执行的总用例数
        total_tests_to_run = len(dynamic_node_pool)
        log(f"[==========] Running {total_tests_to_run} test(s).")

        for idx, node in dynamic_node_pool.items():
            name, id_hex = node["name"], node["id_hex"]
            exp_m = next((d["DTCNumber"] for d in dtcs_def if
                          d["RelatedMessageID"] == id_hex and d["DTCType"] == "MissingMessage"), None)

            # ================= 测试项 A: 丢失报文测试 =================
            test_case_missing = f"Node0x{id_hex}-{name} - testMissingMessage"
            log(f"[ RUN      ] {test_case_missing}")

            execute_service(zcanlib, chn_handle, "Diagnostic Session Control", [0x10, 0x03])
            log(f"Stopping TX for 0x{id_hex}")
            node["active"] = False
            apply_periodic_msgs(zcanlib, device_handle, TARGET_PORT)
            clear_dtcs(zcanlib, chn_handle)
            time.sleep(10)

            found = read_dtcs(zcanlib, chn_handle)

            # 结果判定与记录
            if exp_m in found:
                res_m = "PASS"
                log(f"[       OK ] {test_case_missing}")
            else:
                res_m = "FAIL"
                log(f"Expected DTC {exp_m} NOT found. Actual: {found}", level=logging.ERROR)
                log(f"[  FAILED  ] {test_case_missing}")
                failed_tests_list.append(test_case_missing)  # 记录失败的名字
            test_summary_list.append(res_m)

            # 测试结束后恢复报文发送
            node["active"] = True
            apply_periodic_msgs(zcanlib, device_handle, TARGET_PORT)
            time.sleep(1)

        # ================= 测试结束后的统计输出 =================
        total_exe = len(test_summary_list)
        passed = test_summary_list.count("PASS")
        failed = total_exe - passed

        log(f"[==========] {total_exe} test(s) run.")
        log(f"[  PASSED  ] {passed} test(s).")

        if failed > 0:
            log(f"[  FAILED  ] {failed} test(s), listed below:")
            for failed_name in failed_tests_list:
                log(f"[  FAILED  ] {failed_name}")

            # 可选：最后加上一句总结提示有多少个失败（与GTest风格一致）
            # log(f"\n {failed} FAILED TEST(S)")

    except Exception as e:
        log(f"[ RUN      ] ScriptExecution - FatalError")
        log(f"Exception raised: {str(e)}", level=logging.ERROR)
        log(traceback.format_exc(), level=logging.ERROR)
        log(f"[  FAILED  ] ScriptExecution - FatalError")


if __name__ == "__main__":
    zcanlib = ZCAN()
    device_handle = zcanlib.OpenDevice(DEVICE_TYPE, 0, 0)
    if device_handle == INVALID_DEVICE_HANDLE:
        print("Cannot Open ZLG Device!")
        exit()

    init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    init_cfg.can_type = ZCAN_TYPE_CANFD
    init_cfg.config.canfd.abit_timing = 1048576
    init_cfg.config.canfd.dbit_timing = 1048576
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