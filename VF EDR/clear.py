import json
import datetime
import time
import threading
import os
import sys
import ctypes
from typing import List, Dict, Any

# 导入 zlgcan 驱动库中的必要定义
try:
    from zlgcan import (
        ZCAN,
        ZCAN_Transmit_Data,
        ZCAN_CHANNEL_INIT_CONFIG,
        ZCAN_STATUS_OK,
        ZCAN_USBCANFD_200U,
    )
except ImportError:
    print("错误: 缺少 zlgcan 驱动库，请确保 zlgcan.py 与本脚本处于同一目录中。")
    sys.exit(1)

# ==================== 硬件及通道配置 ====================
DEVICE_TYPE = ZCAN_USBCANFD_200U
DEVICE_INDEX = 0
CHANNEL_INDEX = 0
BAUD_RATE = "500000"  # 波特率 500k

# ==================== 诊断寻址配置 ====================
DiagnosticAddressing = {
    "EDRReqAddressing": 0x688,  # 请求 ID
    "EDRResAddressing": 0x608  # 响应 ID
}

# ==================== 唤醒/前置周期报文配置 ====================
# 为保证诊断动作能够顺利执行，在运行清除流程期间，后台会自动持续发送这些 ACU 激活报文
acu_preconditions = [
    {"id": 0x20D, "data": [0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], "ms": 20},
    {"id": 0x112, "data": [0x2D, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00], "ms": 100},
    {"id": 0x40D, "data": [0x17, 0x00, 0x00, 0x00, 0x00, 0x07, 0xD0, 0x00], "ms": 120},
]

stop_periodic = False
periodic_messages = {}
periodic_lock = threading.Lock()

# ==================== 日志初始化 ====================
LOG_DIR = "Logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_file_path = os.path.join(LOG_DIR, f"Clear_Tool_Run_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")


def write_log(msg: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    log_line = f"[{timestamp}] {msg}"
    print(log_line)
    try:
        with open(log_file_path, "a", encoding="utf-8") as f:
            f.write(log_line + "\n")
    except Exception:
        pass


# ==================== 安全解锁算法库加载与计算 ====================
current_dir = os.path.dirname(os.path.abspath(__file__))
ALGO_DLL_PATH = os.path.join(current_dir, "VF65ZLGDll.dll")
dll = None

try:
    if sys.version_info >= (3, 8):
        os.add_dll_directory(current_dir)
    dll = ctypes.CDLL(ALGO_DLL_PATH)
    write_log(f"✅ 安全算法库加载成功: {ALGO_DLL_PATH}")
except Exception as e:
    write_log(f"❌ 安全算法库加载失败: {e}，将无法运行安全解锁步骤（0x27）。")
    dll = None


def calculate_key_from_seed(seed_bytes: List[int], security_level: int = 3, variant: str = "VF6.PY") -> List[int]:
    if dll is None:
        raise RuntimeError("算法库未成功加载，无法计算 Key。")
    try:
        seed_array_c = (ctypes.c_ubyte * len(seed_bytes))(*seed_bytes)
        seed_array_size = ctypes.c_ushort(len(seed_bytes))
        security_level_c = ctypes.c_uint(security_level)
        variant_c = ctypes.c_char_p(variant.encode('utf-8'))

        key_array = (ctypes.c_ubyte * 16)()
        key_array_size = ctypes.c_ushort(16)

        result = dll.ZLGKey(
            seed_array_c, seed_array_size, security_level_c, variant_c,
            key_array, ctypes.byref(key_array_size)
        )
        if result != 0:
            raise RuntimeError(f"ZLGKey calculation failed with error code: {result}")
        return list(key_array[:key_array_size.value])
    except Exception as e:
        write_log(f"ZLGKey 算法计算失败: {e}")
        raise


# ==================== 底层 CAN 发送与周期线程 ====================
def send_raw_can(can_dll: ZCAN, chn_handle: int, can_id: int, data: List[int]) -> bool:
    transmit_obj = ZCAN_Transmit_Data()
    transmit_obj.transmit_type = 0  # 正常发送
    transmit_obj.frame.can_id = can_id
    transmit_obj.frame.can_dlc = len(data)
    for idx, val in enumerate(data):
        transmit_obj.frame.data[idx] = val

    ret = can_dll.Transmit(chn_handle, transmit_obj, 1)
    return ret == ZCAN_STATUS_OK


def periodic_sender_loop(can_dll: ZCAN, chn_handle: int):
    global stop_periodic
    while not stop_periodic:
        now = time.time()
        with periodic_lock:
            for can_id, msg in list(periodic_messages.items()):
                if now >= msg["next_send"]:
                    send_raw_can(can_dll, chn_handle, can_id, msg["data"])
                    msg["next_send"] = now + (msg["ms"] / 1000.0)
        time.sleep(0.001)


# ==================== ISO 15765-2 (CAN-TP) 协议传输 ====================
def tp_transmit(can_dll: ZCAN, chn_handle: int, req_id: int, res_id: int, payload: List[int], timeout_ms=2000) -> bool:
    total_len = len(payload)
    if total_len <= 7:
        sf_frame = [total_len] + payload
        while len(sf_frame) < 8:
            sf_frame.append(0x00)
        return send_raw_can(can_dll, chn_handle, req_id, sf_frame)
    else:
        # 多帧发送首帧
        ff_pci0 = 0x10 | ((total_len >> 8) & 0x0F)
        ff_pci1 = total_len & 0xFF
        ff_frame = [ff_pci0, ff_pci1] + payload[:6]

        can_dll.ClearBuffer(chn_handle)
        if not send_raw_can(can_dll, chn_handle, req_id, ff_frame):
            return False

        # 等待流控制帧
        fc_received = False
        st_min_ms = 10
        start_time = time.time()

        while (time.time() - start_time) < (timeout_ms / 1000.0):
            num = can_dll.GetReceiveNum(chn_handle, 0)
            if num > 0:
                msgs, ret_num = can_dll.Receive(chn_handle, num, 50)
                for i in range(ret_num):
                    msg = msgs[i].frame
                    if msg.can_id == res_id:
                        frame_data = list(msg.data[:msg.can_dlc])
                        if frame_data and (frame_data[0] & 0xF0) == 0x30:
                            st_min_val = frame_data[2]
                            if st_min_val <= 0x7F:
                                st_min_ms = st_min_val
                            elif 0xF1 <= st_min_val <= 0xF9:
                                st_min_ms = 1
                            fc_received = True
                            break
                if fc_received:
                    break
            time.sleep(0.001)

        if not fc_received:
            write_log("等待流控制帧 (FC) 超时。")
            return False

        # 发送连续帧
        remaining_data = payload[6:]
        sn = 1
        chunk_size = 7

        for i in range(0, len(remaining_data), chunk_size):
            chunk = remaining_data[i:i + chunk_size]
            cf_pci = 0x20 | (sn & 0x0F)
            cf_frame = [cf_pci] + chunk
            while len(cf_frame) < 8:
                cf_frame.append(0x00)

            time.sleep(st_min_ms / 1000.0)
            if not send_raw_can(can_dll, chn_handle, req_id, cf_frame):
                return False
            sn = (sn + 1) % 16

        return True


def tp_receive(can_dll: ZCAN, chn_handle: int, res_id: int, req_id: int, timeout_ms=3000):
    buffer = []
    expected_len = 0
    received_len = 0
    seq = 1
    start_time = time.time()

    while (time.time() - start_time) < (timeout_ms / 1000.0):
        num = can_dll.GetReceiveNum(chn_handle, 0)
        if num > 0:
            msgs, ret_num = can_dll.Receive(chn_handle, num, 50)
            for i in range(ret_num):
                msg = msgs[i].frame
                if msg.can_id == res_id:
                    frame_data = list(msg.data[:msg.can_dlc])
                    if not frame_data:
                        continue

                    pci = frame_data[0] & 0xF0
                    if pci == 0x00:
                        sf_len = frame_data[0] & 0x0F
                        return frame_data[1:1 + sf_len]
                    elif pci == 0x10:
                        expected_len = ((frame_data[0] & 0x0F) << 8) | frame_data[1]
                        buffer = frame_data[2:]
                        received_len = len(buffer)

                        fc_frame = [0x30, 0x00, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x00]
                        send_raw_can(can_dll, chn_handle, req_id, fc_frame)
                        seq = 1
                        start_time = time.time()
                    elif pci == 0x20:
                        if expected_len == 0:
                            continue
                        payload = frame_data[1:]
                        remaining = expected_len - received_len
                        if remaining < len(payload):
                            payload = payload[:remaining]

                        buffer.extend(payload)
                        received_len += len(payload)
                        seq = (seq + 1) & 0x0F
                        start_time = time.time()

                        if received_len >= expected_len:
                            return buffer
        else:
            time.sleep(0.001)
    return None


def uds_request(can_dll: ZCAN, chn_handle: int, UDSSID: int, data: List[int]):
    payload = [UDSSID] + data
    req_id = DiagnosticAddressing["EDRReqAddressing"]
    res_id = DiagnosticAddressing["EDRResAddressing"]

    tx_log = ("%02X " % UDSSID) + " ".join('{:02X}'.format(a) for a in data)
    write_log(f"[UDS Tx] {req_id:03X}  {tx_log}")

    if not tp_transmit(can_dll, chn_handle, req_id, res_id, payload):
        write_log("传输错误: CAN-TP 发送失败。")
        return None

    response_payload = tp_receive(can_dll, chn_handle, res_id, req_id, timeout_ms=3000)
    if response_payload is None:
        write_log("传输错误: 接收响应超时。")
        return None

    rx_log = " ".join('{:02X}'.format(a) for a in response_payload)
    if response_payload[0] == UDSSID + 0x40:
        write_log(f"[UDS Rx] {res_id:03X}  {rx_log}  (Positive)")
    elif response_payload[0] == 0x7f:
        write_log(f"[UDS Rx] {res_id:03X}  {rx_log}  (Negative)")
    else:
        write_log(f"[UDS Rx] {res_id:03X}  {rx_log}  (Unknown)")

    return response_payload


# ==================== 清除核心序列执行逻辑 ====================
def execute_clear_sequence(can_dll: ZCAN, chn_handle: int, clear_file_path="ClearSequence.json") -> bool:
    write_log("====== 启动 ACU 诊断清除与重置流程 ======")

    if not os.path.exists(clear_file_path):
        write_log(f"错误: 未找到清除序列配置文件 {clear_file_path}，无法继续运行。")
        return False

    try:
        with open(clear_file_path, 'r', encoding='utf-8') as f:
            steps = json.load(f)
    except Exception as e:
        write_log(f"解析清除配置文件错误: {e}")
        return False

    max_runs = 3
    for run in range(1, max_runs + 1):
        write_log(f"开始执行诊断重置流程 (运行次数: {run}/{max_runs})...")
        run_success = True

        for idx, step in enumerate(steps, 1):
            name = step.get("name", f"步骤_{idx}")

            # 延时指令处理
            if "delay" in step:
                delay_ms = step["delay"]
                write_log(f"[延时] 等待 {delay_ms} ms...")
                time.sleep(delay_ms / 1000.0)
                continue

            if "request" in step:
                req_str = step["request"]
                req_bytes = [int(x, 16) for x in req_str.split()]
                if not req_bytes:
                    continue
                sid = req_bytes[0]
                params = req_bytes[1:]

                write_log(f"--> 步骤 {idx}: {name}")

                # ------------------ 安全解锁机制处理 ------------------
                if sid == 0x27 and params == [0x03]:
                    # 1. 发送 27 03 获取 Seed
                    res = uds_request(can_dll, chn_handle, 0x27, [0x03])
                    if res is None or len(res) < 3 or res[0] != 0x67 or res[1] != 0x03:
                        write_log(f"[-] 步骤 {idx} [{name}] 失败: 获取 Seed 异常。")
                        run_success = False
                        break

                    seed = res[2:]
                    try:
                        key = calculate_key_from_seed(seed, security_level=3, variant="VF6.PY")
                    except Exception as e:
                        write_log(f"[-] 步骤 {idx} [{name}] 失败: 密匙计算失败({e})。")
                        run_success = False
                        break

                    # 2. 发送 27 04 进行 Key 校验
                    key_res = uds_request(can_dll, chn_handle, 0x27, [0x04] + key)
                    if key_res is None or len(key_res) < 2 or key_res[0] != 0x67 or key_res[1] != 0x04:
                        write_log(f"[-] 步骤 {idx} [{name}] 失败: 密匙验证未通过。")
                        run_success = False
                        break

                    time.sleep(0.1)
                    continue

                # ------------------ 常规 UDS 诊断执行 ------------------
                res = uds_request(can_dll, chn_handle, sid, params)

                if res is None or res[0] == 0x7F:
                    write_log(f"[-] 步骤 {idx} [{name}] 失败: 诊断服务无响应或返回否定响应。")
                    run_success = False
                    break

                # 校验预期回复
                expected_prefix = step.get("response", "")
                if expected_prefix:
                    expected_bytes = [int(x, 16) for x in expected_prefix.split()]
                    match = True
                    for i, b in enumerate(expected_bytes):
                        if i < len(res) and res[i] != b:
                            match = False
                            break
                    if not match:
                        write_log(f"[-] 步骤 {idx} [{name}] 失败: 响应内容与预期不符。")
                        run_success = False
                        break

                time.sleep(0.1)

        if run_success:
            write_log("====== 诊断清除与重置流程执行完毕（全部步骤校验通过） ======")
            return True
        else:
            if run < max_runs:
                write_log(f"第 {run} 次流程执行失败，将在 2 秒后尝试重试...")
                time.sleep(2.0)

    write_log("[-] 严重错误: 已达到重试上限，流程执行失败。")
    return False


# ==================== 主运行入口 ====================
def main():
    global stop_periodic, periodic_messages

    write_log("====== 运行 EDR 独立清除复位工具 ======")

    # 初始化 CAN 硬件设备
    can_dll = ZCAN()
    dev_handle = can_dll.OpenDevice(DEVICE_TYPE, DEVICE_INDEX, 0)
    if dev_handle == 0:
        write_log("Error: 无法打开 CAN 设备！")
        return

    # 设置波特率
    iproperty = can_dll.GetIProperty(dev_handle)
    if iproperty:
        baud_path = f"info/channel/chn{CHANNEL_INDEX}/baudrate"
        can_dll.SetValue(iproperty, baud_path, BAUD_RATE)
        can_dll.ReleaseIProperty(iproperty)

    init_cfg = ZCAN_CHANNEL_INIT_CONFIG()
    init_cfg.can_type = 0  # 标准 CAN
    init_cfg.config.can.acc_code = 0x00000000
    init_cfg.config.can.acc_mask = 0xFFFFFFFF
    init_cfg.config.can.filter = 0
    init_cfg.config.can.mode = 0

    chn_handle = can_dll.InitCAN(dev_handle, CHANNEL_INDEX, init_cfg)
    if chn_handle == 0:
        write_log("Error: 初始化 CAN 通道失败！")
        can_dll.CloseDevice(dev_handle)
        return

    if can_dll.StartCAN(chn_handle) != ZCAN_STATUS_OK:
        write_log("Error: 启动 CAN 通道失败！")
        can_dll.CloseDevice(dev_handle)
        return

    # 启动前置唤醒报文周期发送线程
    for msg in acu_preconditions:
        periodic_messages[msg["id"]] = {
            "data": msg["data"],
            "ms": msg["ms"],
            "next_send": time.time()
        }

    stop_periodic = False
    sender_thread = threading.Thread(target=periodic_sender_loop, args=(can_dll, chn_handle))
    sender_thread.daemon = True
    sender_thread.start()
    write_log("前置周期仿真线程启动成功，已开始向总线发送激活信号。")

    # 执行清除流程
    try:
        execute_clear_sequence(can_dll, chn_handle, "ClearSequence.json")
    except Exception as e:
        write_log(f"运行清除过程发生未捕获异常: {e}")
    finally:
        # 关闭硬件连接，清理线程资源
        stop_periodic = True
        sender_thread.join(timeout=1.0)
        can_dll.ResetCAN(chn_handle)
        can_dll.CloseDevice(dev_handle)
        write_log("设备连接关闭成功，独立流程结束。")


if __name__ == "__main__":
    main()